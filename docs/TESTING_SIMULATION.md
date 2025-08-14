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

Add this sensor:

```yaml
sensor:
  - platform: integration
    source: sensor.water_test_flow
    name: Water Test Volume (integrated)
    unit_time: min     # flow is in gallons per minute
    method: trapezoidal
    round: 3
```

Then use:
- Flow sensor: `sensor.water_test_flow`
- Volume sensor: `sensor.water_test_volume_integrated`

With this approach, you don’t need the per-second tick script or daemon; set the flow to a value, wait, then set it back to zero to simulate sessions/leaks. The Integration sensor will accumulate volume automatically.

## 3) Scripts to simulate sessions and leaks
Add scripts that “tick” the volume based on flow once per second. If you use the Integration sensor approach above, you can skip this section and instead create simple scripts that set flow for a duration and then stop.

```yaml
script:
  sim_flow_stop:
    alias: Stop Simulated Flow
    sequence:
      - service: input_number.set_value
        data:
          value: 0
        target:
          entity_id: input_number.water_test_flow_gpm

  sim_leak_start:
    alias: Start Simulated Leak (0.2 gpm for 20 min)
    mode: restart
    sequence:
      - service: input_number.set_value
        data:
          value: 0.2
        target:
          entity_id: input_number.water_test_flow_gpm
      - repeat:
          count: 1200   # 20 minutes
          sequence:
            - service: input_number.set_value
              data:
                value: >-
                  {{ (states('input_number.water_test_volume_gal')|float(0)) +
                     (states('input_number.water_test_flow_gpm')|float(0) / 60.0) }}
              target:
                entity_id: input_number.water_test_volume_gal
            - delay: "00:00:01"

  sim_normal_shower:
    alias: Simulate Normal Shower (2.2 gpm for 8 min)
    mode: restart
    sequence:
      - service: input_number.set_value
        data:
          value: 2.2
        target:
          entity_id: input_number.water_test_flow_gpm
      - repeat:
          count: 480   # 8 minutes
          sequence:
            - service: input_number.set_value
              data:
                value: >-
                  {{ (states('input_number.water_test_volume_gal')|float(0)) +
                     (states('input_number.water_test_flow_gpm')|float(0) / 60.0) }}
              target:
                entity_id: input_number.water_test_volume_gal
            - delay: "00:00:01"
      - service: script.sim_flow_stop
```

Tip: You can add more scripts (e.g., dishwasher at 0.6 gpm for 45 min) to build “normal” behavior.

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
