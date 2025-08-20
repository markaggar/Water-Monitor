![Version](https://img.shields.io/github/v/release/markaggar/Water-Monitor?style=for-the-badge)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
# Water Monitor

A Home Assistant custom integration for intelligent water usage monitoring with robust session tracking, gap handling, hot water analytics, and optional leak detection. Only a Flow sensor is required; a Volume sensor is optional (used as the source of truth when provided). Multi‑instance friendly, fully configurable in the UI, and clean sensor naming to avoid collisions.

<img width="449" height="666" alt="image" src="https://github.com/user-attachments/assets/a8cdcfeb-f03d-4e9c-9527-e7230c58ddd8" />

## Highlights

- Smart session detection with gap handling; duration and averages exclude gap time
- Works with Flow‑only (integrates volume for you) or with a Volume sensor (uses it directly)
- Transparent attributes: integration_method (external_volume_sensor | trapezoidal | left), sampling intervals, and more
- Hot water analytics via an optional binary sensor
- Optional leak detectors:
  - Low‑flow leak: detects continuous “dribble” with seed/persistence, smoothing, cooldown, and high‑flow clear
  - Tank refill leak: detects clustered, similar‑sized refills; contributing_events include local_time for notifications
  - Intelligent Leak: Model-based leak detection (experimental, testers wanted!)
- Synthetic flow test control (Number) for safe, repeatable simulations (no need to waste actual water)
- Shutoff valve support
  - Optionally link a shutoff valve entity (switch, input_boolean, or valve)
  - Per-detector auto-shutoff toggles: auto-shutoff can be enabled for each leak detector
  - Leak sensors will not clear while the valve is off, ensuring you don't miss a leak event
- Upstream sensors health (binary sensor)
  - Monitors availability/validity of the configured upstream sensors (flow, volume, shutoff valve and hot-water)
- Reconfigurable via Options
  - Adjust sensors and thresholds at any time in the integration’s Configure dialog
  - Optional sensor name prefix for easy disambiguation in the UI
- Multi-instance safe
  - Add multiple instances with different sensors and thresholds

## Devices
Here is a list of devices that the community has tested with the integration (submit an issue to add your experience with a device)

| Device | Manufacturer | Works with Integration | Flow Sensor | Volume Sensor | Shutoff Valve | Local API | Flow/Volume Sensor Latency | Plumbing Required | Link |
|--------|--------------|------------------------|-------------|---------------|---------------|-----------|----------------------------|-------------------|------|
| Droplet | Hydrific Water | Y (Flow) | Y | N | N | Y (MQTT) | <3s | N | [link](https://shop.hydrificwater.com/pages/buy-droplet) |
| Flowsmart All-in-one | Yolink | Y (Valve) | N | Y | Y | N | minutes | Y | [link](https://shop.yosmart.com/products/ys5008-20) |
| Titan Water Valve Actuator | Zooz | Y (Valve) | N | N | Y| Y (Zwave) | NA | N | [link](https://amzn.to/4mPD3x8) |


## DISCLAIMER ##
A water flow monitor does not replace the need for leak/moisture sensors placed in strategic locations around your home. If a leak is due to a failure of an appliance (e.g. leaky hose under the sink that only occurs when the faucet is turned on or a sudden failure of a rusty water heater, washing machine, toilet o-ring), water infiltration from outside, or a blocked sewer pipe (speaking from experience), a water flow sensor (and this integration) will not detect those events. It is best suited for wasted water scenarios (e.g. faucet left on, toilet flapper not sealing) or burst pipes (e.g. outside hoses, pipes behind walls) where you cannot practically place a leak/moisture sensor (again, experienced all of those!).

Also, having a controllable valve that enables you or an automation to remotely shut off water to the house in the event of a leak detection (from either this integration or a leak/moisture sensor) could pay for itself many times over if you ever have a leak detected but are not at home to turn the water off manually.

**Finally, your use of this integration means you agree that the author(s) of this integration bear no responsibility for leaks that are not detected or notified, due to any cause. It is important that you do your own testing, particularly ensuring that the parameters you set make sense for your situation**.

## Learn more

- Full README: [github.com/markaggar/Water-Monitor](https://github.com/markaggar/Water-Monitor#readme)
- Releases: [github.com/markaggar/Water-Monitor/releases](https://github.com/markaggar/Water-Monitor/releases)
- Source: [github.com/markaggar/Water-Monitor](https://github.com/markaggar/Water-Monitor)
