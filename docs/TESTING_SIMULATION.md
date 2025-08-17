# Testing Intelligent Leak Detection without using real water

This guide shows how to test the Intelligent Leak sensor safely by simulating flow/volume in Home Assistant.

 
## 1) Seed baselines (optional but recommended)
 
Use the built-in service to generate historical sessions so baselines exist:

- Call service `water_monitor.simulate_history` with:
  - `days`: 14 (or more)
  - `include_irrigation`: true/false
  - `entry_id`: optional (leave empty to run for all entries)

This only seeds the engine’s learning; it does not generate live sessions.

 
## 2) Create controllable test sensors (no hardware)
 
We’ll drive two helpers and expose them via Template Sensors that your integration can point at for flow and volume.

Add to your `configuration.yaml` (or a package):

```yaml
input_number:
  water_test_flow_gpm:
    name: Water Test Flow (gpm)
    min: 0
    max: 10
    step: 0.1
  water_test_volume_gal:
    name: Water Test Volume (gal)
    min: 0
    max: 10000
    step: 0.01

sensor:
  - platform: template
    sensors:
      water_test_flow:
        friendly_name: "Water Test Flow"
        unit_of_measurement: "gpm"
        value_template: "{{ states('input_number.water_test_flow_gpm') | float(0) }}"
      water_test_volume:
        friendly_name: "Water Test Volume"
        unit_of_measurement: "gal"
        value_template: "{{ states('input_number.water_test_volume_gal') | float(0) }}"
```

Reload helpers and template entities (or restart HA).

Then configure the Water Monitor integration to use:

- Flow sensor: `sensor.water_test_flow`
- Volume sensor: `sensor.water_test_volume`
- Hot water sensor: leave blank (optional)

 
### Alternative (recommended): Integration sensor for volume
 
Instead of manually ticking volume, create an Integration sensor that converts flow (gpm) into cumulative volume (gal). This is simpler and more realistic, and works perfectly with the integration’s session tracking (it subtracts the starting total per session).

Add this sensor (use Left Riemann to avoid jumps):

```yaml
sensor:
  - platform: integration
    source: sensor.water_test_flow
    name: Water Test Volume (integrated)
    unit_time: min     # flow is in gallons per minute
    method: left       # left Riemann sum avoids trapezoidal spikes
    round: 3
```

Then use:

- Flow sensor: `sensor.water_test_flow`
- Volume sensor: `sensor.water_test_volume_integrated`

With this approach, you don’t need the per-second tick script or daemon; set the flow to a value, wait, then set it back to zero to simulate sessions/leaks. The Integration sensor will accumulate volume automatically.

 
## 3) Use the Integration-based simulation package (recommended)
 
Use the package `docs/examples/water_monitor_simulation_package_integration.yaml` to simulate flow directly in HA. It:

- Creates a sampled flow sensor and integrates it to volume (left Riemann).
- Provides scripts for fixed events (e.g., normal shower) and a delta-based adjuster.
- Adds parallel, randomized event scripts (faucet, shower, washer, toilet, dishwasher, irrigation) that add a flow delta on start and subtract it on completion.
- Includes a dedicated Tank Leak simulation with small periodic jitter.

Enable randomized usage by turning on `input_boolean.sim_random_usage_enabled`. Start/stop the tank leak with `script.sim_tank_leak_start` / `script.sim_tank_leak_stop`.

 
## 4) Run a test
 
- Run `script.sim_normal_shower` a few times (and/or call `water_monitor.simulate_history`) to build baselines.
- Start a leak with `script.sim_leak_start`.
- Watch `binary_sensor.*intelligent_leak` attributes:
  - `baseline_ready`, `chosen_percentile`, `effective_threshold_s`, `risk`, `reasons`.
- Adjust “Leak alert sensitivity” number entity if you want it stricter/looser.

 
## 5) Cleanup/Reset
 
- Run `script.sim_flow_stop` to end a session.
- Set `input_number.water_test_volume_gal` back to 0 if you want a fresh start.

 
## Alternative: MQTT sensors
 
If you prefer, create two MQTT sensors and publish values via `mqtt.publish` service. The logic is the same: set flow, increment volume each second.

 
## Notes
 
- The integration reads numeric `state` from your configured flow/volume sensors; units are for display only.
- The engine’s baseline learning is separate from the real-time tracker; you can seed history then simulate live sessions as above.
