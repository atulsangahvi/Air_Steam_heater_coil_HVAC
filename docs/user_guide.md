# User Guide

## Getting Started

### Installation
1. Install Python 3.8+
2. Install required packages:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the app:
   ```bash
   streamlit run steam_air_heater_coil_app.py
   ```

## App purpose
This app is for **steam air heater coils used for AHU reheat** after a cooling coil. The entering air may be near 100% RH, but the steam coil is treated as a **dry sensible heating coil**. Therefore:
- humidity ratio is held constant across the steam coil
- dry bulb increases
- wet bulb increases modestly
- dew point stays essentially unchanged
- relative humidity decreases

## Workflow

### 1. Choose the run mode
- **Rating: given steam flow**
- **Sizing: find steam flow for target leaving DB**

### 2. Enter coil geometry
Use the same style of geometry used in the DX evaporator project:
- face width
- face height
- row pitch St
- longitudinal pitch Sl
- number of rows
- tube OD
- tube wall thickness
- fins per inch
- fin thickness
- fin material
- tube material
- circuits
- sigma free area

### 3. Enter air-side data
Choose air flow as either:
- face velocity, or
- volume flow

Choose air inlet condition as either:
- dry bulb + RH, or
- dry bulb + wet bulb

The app calculates the remaining psychrometric properties automatically.

### 4. Enter steam-side data
- pressure basis: bar(g) or bar(abs)
- steam pressure
- inlet state
- wet steam inlet quality if applicable
- superheat if applicable
- allowable condensate subcooling
- steam mass flow in rating mode
- target leaving DB in sizing mode
- header diameters and header length

### 5. Run the analysis
The app reports:
- total duty
- leaving air DB / WB / RH / dew point
- steam flow
- condensate rate
- air-side pressure drop
- steam-side pressure drop
- rows needed to hit target leaving temperature
- row-by-row table

## Understanding the results

### Air side
The most important check in reheat service is that the coil lifts the air to the required leaving dry bulb while keeping humidity ratio unchanged.

### Relative humidity reduction
The app shows RH reduction directly. This is usually the main process purpose in AHU reheat service.

### Rows to target
This indicates whether the selected row count is enough. If the target is not reached even with high steam flow, the coil is likely **UA-limited** rather than steam-flow-limited.

### Condensate outlet phase
The outlet may remain:
- still wet / partly condensing,
- fully condensed at saturation, or
- slightly subcooled if allowed.

## Recommended first checks
For a new design, review these first:
1. Leaving air DB
2. Leaving air RH
3. Total duty
4. Air pressure drop
5. Steam flow required
6. Rows to target
7. Steam outlet phase

## Typical input guidance

### Entering air after cooling coil
Typical reheat entering conditions may be around:
- 11 to 15 C DB
- 90 to 100% RH

### Steam pressure
Many AHU reheat coils use low-pressure steam. Start with the actual available steam pressure at the coil inlet rather than boiler pressure.

### Wet steam quality
If steam quality is uncertain, start with:
- 1.00 for dry saturated supply
- 0.97 to 0.99 if some moisture carryover is expected

### Allowed subcooling
Start with 0 K unless there is a clear reason to model subcooled condensate inside the coil.

## Limitations
- no valve authority calculation
- no control valve Cv sizing
- no trap sizing or stall analysis
- no detailed condensate backing-up analysis
- no steam distribution maldistribution model
- no casing heat loss
- no air bypass fraction model

## Calibration files
The repository includes calibration placeholders so you can later tune:
- air-side pressure drop multiplier
- air-side heat-transfer multiplier
- steam-side single-phase heat-transfer multiplier
- steam-side condensation multiplier
- steam-side pressure-drop multiplier

## Downloads
The app exports:
- row-by-row CSV
- summary JSON

These outputs are useful for keeping design records and building your own calibration database.
