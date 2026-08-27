# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project follows semantic versioning.

## [Unreleased]

## [1.4.0] - 2026-08-16

### Added
- Introduced a two-stage Intelligent Leak detector state model:
  - Potential leak (early warning)
  - Confirmed leak (high-confidence sustained anomaly)
- Added learned profile clustering for session-shape behavior using volume, duration, average flow, and hot water percentage.
- Added profile bootstrapping from persisted engine session history at startup.
- Added Intelligent Leak diagnostics attributes for transparency:
  - leak_stage, risk, trigger_threshold, trigger_reason
  - profile_id, profile_distance, expected_in_context
  - known_profiles, learned_sessions, potential_hold_s, confirmed_hold_s
- Added engine helper API to expose recent sessions for read-only consumers.
- Added a new `water_monitor.reset_intelligent_leak_learning` service to immediately clear and rebuild Intelligent Leak profiles from existing engine session history, instead of waiting for organic relearning (useful for testing and for recovering from profiles learned under the old scoring model).

### Changed
- Reworked Intelligent Leak scoring to prioritize novelty and persistence over strict time-of-day dependency.
- Updated context handling (home/night/away/vacation) to act as a risk modifier instead of the primary baseline source.
- Updated auto-shutoff behavior so Intelligent Leak shutoff actions occur when entering Confirmed stage.
- Updated README documentation for Intelligent Leak behavior and diagnostics.
- Intelligent Leak profiles now track bounded (up to 60) recent samples of duration, volume, and session start-time-of-day, in addition to the existing running-mean centroid.
- Profile-shape distance (`_profile_distance`) now normalizes duration/volume by each profile's own learned spread (p10–p90 range) once enough samples exist, instead of only the mean — so profiles with naturally wide variability (e.g. showers) are less prone to false novelty, while consistent profiles (e.g. a fixed irrigation cycle) stay tightly scored.
- Duration and volume risk components are now capped and based on a learned p95 ceiling per profile (falling back to the previous mean-based heuristic until a profile has at least 5 samples), rather than growing without bound the longer/larger a session runs.
- Added a purely data-driven, learned time-of-day window per profile (derived from observed session start times, no hardcoded times): sessions starting inside a profile's normal window get a bounded discount on duration/volume overrun risk, letting fixed-schedule irrigation and anytime showers each get an appropriately narrow or wide window automatically.
- Added new diagnostic attributes on the Intelligent Leak sensor: `volume_component`, `time_window_match`, `time_window_discount`.
- Intelligent Leak now cross-checks the Low-Flow Leak and Tank Refill Leak detectors during each live session. If either independently confirms a leak, Intelligent Leak latches a strong corroboration signal for the remainder of the session, adds a dominant risk contribution, and bypasses its own hold timers to reach Confirmed quickly — while remaining fully functional on its own scoring if those detectors are disabled or absent (corroboration is a boost, never a hard dependency).
- Sessions that a corroborating detector confirms as a leak are now excluded from Intelligent Leak's "normal" profile learning, so a recurring leak stops being progressively learned as expected usage over time.
- The high-confidence-profile discount now only reduces the novelty risk component, instead of discounting the overall risk total — so genuine duration/volume/low-flow overrun signals are never "forgiven" just because a session superficially resembles a known routine.
- The standalone low-flow risk component now scales with how long the low flow has persisted past the existing 10-minute floor (instead of a flat bump), so a long-running drip accumulates meaningfully more risk on its own, independent of corroboration from other detectors.
- Added new diagnostic attributes on the Intelligent Leak sensor: `corroboration_component`, `corroborated_leak`, `corroboration_source`.
- Added a 30-second sustained-zero-flow debounce before Intelligent Leak honors a flow-based instant-clear from Confirmed stage, mirroring the existing idle-clear pattern already used by Low-Flow Leak. Added new diagnostic attributes: `idle_zero_s`, `clear_idle_s`.

### Fixed
- Fixed spurious Intelligent Leak "potential"/"confirmed" activations on normal, recurring high-volume sessions (e.g. daily irrigation cycles, long showers) that simply ran a bit longer or used a bit more water than the profile's running-average duration/volume. The old scoring used an uncapped, mean-based duration tolerance that could accumulate unbounded risk for any session above the mean, and a volume/duration distance metric normalized purely by the mean rather than the pattern's actual observed variability.
- Fixed Intelligent Leak failing to flag a genuine sustained low-flow leak that the Low-Flow Leak detector caught independently. Intelligent Leak had no awareness of the other leak detectors and relied solely on its own novelty/profile scoring, whose low-flow risk contribution was a small flat bump that could sit well below the alert threshold (and could be partly offset if the drip pattern already resembled a learned "normal" profile).
- Fixed Intelligent Leak rapidly flapping on/off (sometimes dozens of times) instead of clearing cleanly at the end of a leak. A Confirmed alarm was cleared the instant a single tick reported zero flow, with no debounce — so a pulsing/intermittent leak tail (e.g. a cycling toilet flapper, or simple sensor noise around zero) could clear the alarm and have it re-confirm on the very next tick, repeatedly. A momentary zero-flow reading is no longer honored as "the leak stopped" until zero flow has been sustained for 30 seconds.
- Fixed "Last session volume", "Current session volume", and "Last session average flow" sensors intermittently flipping their unit of measurement to/from blank on every Home Assistant restart or integration reload (options change), which triggered HA Repairs warnings and could suppress long-term statistics ("Units change to/from blank", "Statistics issue L / None"). These sensors previously reset their unit to blank on every startup and only re-derived it from the upstream flow/volume sensor once the first live tracker update arrived, and one of them could also blank an already-known unit if a single reading briefly lacked a unit attribute. The last-known-good unit is now persisted per config entry and seeded immediately on startup/reload, and unit derivation is now "sticky" (never regresses to blank) — it's only relearned from scratch if the configured flow/volume sensor entity itself is changed in the integration's options.

### Notes
- Intelligent Leak remains experimental.

## [1.3.2] - 2025-12-08

### Fixed
- Normalized flow units to prevent gpm/gal/min oscillation in statistics and charts.

## [1.3.2-beta1] - 2025-11-30 (Pre-release)

### Changed
- Attempted fix for unit switching between gal/min and gpm that could break statistics.

## [1.3.1] - 2025-08-31

### Added
- Intelligent leak learning period configuration controls.
- Notification control helpers for intelligent leak and high-usage alerts.

### Changed
- Improved upstream health notifications with specific sensor status details.
- Updated automation package with more granular notification controls.

### Fixed
- Corrected corrupted translation files.

## [1.3.0] - 2025-08-29

### Added
- Multi-language support (Spanish, German, French).
- Hot water percentage threshold for tank refill leak detection.

### Changed
- Standardized leak detector attributes.
- Improved intelligent leak detector configuration and entity cleanup behavior.
- Updated automation package to align with standardized attributes.

## [1.2.0] - 2025-08-22

### Added
- Complete Home Assistant automation package with actionable notifications.
- Unified orchestrator automation with reminder/snooze workflows and valve integration.
- Template-based high session detection to reduce notification noise.

### Fixed
- Synthetic volume integration for flow-based calculations.
- Tank refill valve attribute updates and synthetic capture behavior.
- Multiple synthetic flow timing and race-condition issues.

### Changed
- Consolidated leak detector and notification behavior across leak types.
- Improved storage cleanup and persistence behavior.

## [1.1.1] - 2025-08-20

### Fixed
- Upstream health sensor connectivity regression when monitoring varying numbers of upstream sensors.

## [1.1.0] - 2025-08-20

### Added
- Water shut-off valve support (switch, input_boolean, valve).
- Per-detector auto-shutoff behavior for leak detectors.

### Changed
- Leak sensors remain active while valve is off to preserve leak visibility until recovery.

## [1.0.1] - 2025-08-19

### Added
- User-friendly local time in tank refill `contributing_events` attributes.

## [1.0.0] - 2025-08-17

### Added
- First HACS release submission for Water Monitor.

## [0.4.0] - 2025-08-17

### Added
- Flow-only setup support with integrated volume when no external volume sensor is configured.

### Changed
- Improved UI clarity, attributes, and persistence behavior for flow-only mode.

## [0.3.0] - 2025-08-16

### Added
- Synthetic flow feature set for testing and simulation workflows.

### Changed
- Deployment script behavior updated to restart Home Assistant only when files change.

## [0.2.2] - 2025-08-13

### Changed
- Upstream health feature refinements and related fixes.

## [0.2.1] - 2025-08-12

### Added
- Health sensors and improvements for low-flow and tank-related sensors.

## [0.2.0] - 2025-08-12

### Added
- Upstream health binary sensor.
- Low-flow and tank refill leak detectors with accompanying documentation updates.

## [0.1.8] - 2025-08-11

### Changed
- Updated `info.md` for improved HACS store metadata handling.

## [0.1.7] - 2025-08-11

### Added
- New sensors, including an early leaky toilet tank detector.

## [0.1.4] - 2025-08-11 (Pre-release)

### Added
- Initial Home Assistant Water Monitor integration release.
- Core sensors: current session volume, last session volume, and low-flow leak detection.

## [0.1-beta] - 2025-08-11 (Pre-release)

### Added
- Initial beta tag for the first integration release.
