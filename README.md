# Steam Air Heater Coil Designer

Streamlit-based HVAC coil rating and sizing tool for **steam reheat coils** used in AHUs after a cooling coil.

This app keeps the **same general coil geometry style** as the DX evaporator project: face size, tube OD, tube wall thickness, row pitch, longitudinal pitch, rows, fins per inch, fin thickness, circuits, fin material, tube material, and air-side compact-fin options.

The intended duty is the usual post-cooling-coil reheat case:
- air entering the steam coil may be near saturation
- the steam coil performs **dry sensible heating only**
- humidity ratio remains essentially constant through the reheat coil
- dry bulb rises and relative humidity falls

## Features

### Main calculation modes
- **Rating mode**: given steam flow, predict leaving air condition and duty
- **Sizing mode**: solve steam flow needed for a target leaving dry bulb temperature

### Coil / thermal model
- Row-by-row marching
- Dry reheat of moist air at constant humidity ratio
- Steam-side treatment for:
  - superheated steam inlet
  - wet steam inlet quality
  - saturated dry steam inlet
  - condensing region
  - optional condensate subcooling
- Air-side compact-fin logic with optional louver enhancement
- Fin efficiency and overall surface efficiency treatment
- Steam-side and air-side pressure-drop estimates

### Outputs
- Duty
- Leaving air DB / WB / RH / dew point
- RH reduction through reheat
- Steam flow and condensate rate
- Estimated steam-side and air-side pressure drop
- Rows needed to meet the target leaving temperature
- Row-by-row results table
- Downloadable CSV and JSON outputs

## Repository structure

```text
steam_air_heater_coil_repo/
├── calibration/
│   ├── backup/
│   │   └── calibration_backup_20240101.json
│   └── calibration_data.json
├── docs/
│   ├── theory.md
│   └── user_guide.md
├── examples/
│   └── example_1_low_pressure_steam.json
├── test_data/
│   └── test_record_template.json
├── .gitignore
├── README.md
├── requirements.txt
└── steam_air_heater_coil_app.py
```

## Installation

```bash
pip install -r requirements.txt
streamlit run steam_air_heater_coil_app.py
```

## Inputs expected by the app

### Coil geometry
- Face width and height
- Number of rows
- Row pitch and longitudinal pitch
- Tube OD and tube wall thickness
- FPI and fin thickness
- Fin and tube material
- Number of circuits
- Air free-flow area ratio sigma

### Air side
- Airflow by face velocity or volume flow
- Inlet air by:
  - dry bulb + RH, or
  - dry bulb + wet bulb

### Steam side
- Steam pressure in bar(g) or bar(abs)
- Inlet state:
  - saturated dry steam
  - wet steam
  - superheated steam
- Wet steam inlet quality
- Steam superheat
- Steam mass flow for rating mode
- Target leaving DB for sizing mode
- Header diameters and header length
- Allowed condensate subcooling

## Typical AHU reheat use case

1. Cooling coil leaves air at low DB and high RH, often near saturation.
2. Steam reheat coil raises the dry bulb temperature.
3. Humidity ratio stays constant across the steam coil.
4. Relative humidity drops to the required supply condition.

Example:
- Air entering steam coil: 13.5 C, 95% RH
- Air leaving steam coil: 18 C
- Humidity ratio unchanged
- RH drops because saturation humidity ratio increases with temperature

## Current modeling scope

Included:
- steady-state dry reheat
- row-by-row thermal march
- simplified steam condensation heat transfer model
- estimated steam header / tube pressure-drop treatment

Not yet included:
- control valve sizing
- steam trap sizing
- vacuum breaker / stall analysis
- non-uniform steam distribution among circuits
- freeze protection checks
- casing bypass leakage / air maldistribution
- detailed stratification across tall coils

## Notes

- The app is intended as a practical engineering design and rating tool, not a certified performance selection program.
- Validate against vendor data or AHU test data before using it for critical commitments.
- Calibration placeholders are included so the model can be tuned later to your own factory or field data.
