# Water Monitor

Custom Home Assistant integration for tracking water usage sessions and detecting sustained low-flow leaks.

- Last session volume and current session volume sensors
- Optional low-flow leak binary sensor with configurable thresholds and timing
- Works with separate flow and total volume sensors, and optional hot-water binary sensor

## Installation

### HACS (recommended)

1. In HACS, add a custom repository:
   - URL: `https://github.com/markaggar/Water-Monitor`
   - Category: Integration
2. Install "Water Monitor"
3. Restart Home Assistant

### Manual

- Copy the `custom_components/water_monitor` folder to your Home Assistant `custom_components` directory.
- Restart Home Assistant.

## Configuration

Settings → Devices & Services → Add integration → Water Monitor

- Page 1:
  - Flow sensor (entity_id)
  - Volume sensor (entity_id)
  - Hot water binary sensor (optional)
  - Session rules: minimum volume, minimum duration, gap tolerance, continuity window
  - Enable low-flow leak detection (toggle)
- Page 2 (when low-flow is enabled):
  - Max low-flow rate
  - Seed low-flow duration
  - Minimum duration to trigger
  - Clear after idle (zero flow)
  - Counting mode:
    - Any non-zero flow (wall clock)
    - Only time within low-flow range
  - Smoothing window
  - Cooldown
  - Clear after sustained high flow (optional)

Options flow mirrors the same two pages.

## Entities

- Sensor: Water Monitor Last session volume
- Sensor: Water Monitor Current session volume
- Binary Sensor: Water Monitor Low-flow leak (when enabled)

## Support

- Documentation: https://github.com/markaggar/Water-Monitor
- Issues: https://github.com/markaggar/Water-Monitor/issues

## Development

- Validate with hassfest and HACS actions in `.github/workflows`
- Bump `version` in `manifest.json` and create a GitHub release for HACS updates
- Domain folder must be `custom_components/water_monitor`
