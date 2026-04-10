# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project follows semantic versioning.

## [1.4.0] - 2026-04-10

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

### Changed
- Reworked Intelligent Leak scoring to prioritize novelty and persistence over strict time-of-day dependency.
- Updated context handling (home/night/away/vacation) to act as a risk modifier instead of the primary baseline source.
- Updated auto-shutoff behavior so Intelligent Leak shutoff actions occur when entering Confirmed stage.
- Updated README documentation for Intelligent Leak behavior and diagnostics.

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
