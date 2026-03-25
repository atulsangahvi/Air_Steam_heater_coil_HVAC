# Theory and Correlations

## Overview
This document explains the theoretical basis used in the Steam Air Heater Coil Designer for AHU reheat duty.

The application models a **finned tube steam coil** heating moist air after a cooling coil. The air side is usually near saturation at coil inlet, but the steam coil itself is treated as a **dry sensible heating coil**.

## Psychrometrics

### Basic assumption for steam reheat
The steam coil does not remove or add moisture under normal reheat operation. Therefore:
- humidity ratio remains constant
- dew point remains essentially constant
- dry bulb rises
- relative humidity drops

### Moist air relationships
The app uses standard HVAC psychrometric relations for:
1. saturation pressure of water
2. humidity ratio from DB and RH
3. humidity ratio from DB and WB
4. moist air enthalpy
5. moist air density
6. RH from DB and humidity ratio
7. wet bulb from DB and humidity ratio
8. dew point from humidity ratio

## Coil geometry treatment
The model uses the same general coil geometry style as the DX evaporator app:
- face width
- face height
- row count
- transverse pitch St
- longitudinal pitch Sl
- tube OD and wall thickness
- fin thickness and FPI
- circuits
- fin and tube materials

From this it estimates:
- face area
- fins count
- tubes per row
- total tubes
- bare tube outside area
- fin area
- total outside area
- minimum free area
- row depth and total coil depth

## Air-side heat transfer and pressure drop
The air-side model follows compact heat exchanger style logic.

### Air properties
Air viscosity, conductivity, density, and cp are evaluated from the local mean air condition.

### Base heat transfer coefficient
A simplified compact-coil correlation is used through a hydraulic-diameter style treatment of the free-flow passage.

### Fin enhancement
Two options are supported:
- wavy fins without louvers
- wavy fins with louvers

User multipliers are provided for:
- air-side heat transfer
- air-side pressure drop

These are practical calibration handles for matching vendor or test data.

### Pressure drop
Air pressure drop is estimated from a Darcy-style friction treatment with geometry-based flow area and optional enhancement factors.

## Fin efficiency
The model uses a Schmidt-type annular fin efficiency treatment.

### Fin efficiency
For a characteristic fin parameter:
- `m = sqrt(2 h / (k_fin t_fin))`
- `eta_f = tanh(X) / X`

### Overall surface efficiency
- `eta_o = 1 - (A_fin / A_total) * (1 - eta_f)`

This reduces the effective air-side area used in the overall UA.

## Steam-side treatment

### Steam inlet states
The steam side can start as:
- saturated dry steam
- wet steam with user-defined inlet quality
- superheated steam

### Three possible thermal regions
The model can march through these regions row by row:
1. superheated steam cooling to saturation
2. condensation at approximately saturation temperature
3. condensate subcooling, if allowed by the user

### Single-phase steam / condensate heat transfer
A Gnielinski-style internal forced convection treatment is used for the single-phase region with a Churchill-type friction factor.

### Condensing heat transfer
For the condensing zone, the model applies a practical Shah-style enhancement on liquid-only heat transfer to represent higher condensation heat transfer coefficients inside the tubes.

This is not a full mechanistic annular / stratified condensation solver, but it is suitable as a practical engineering model for finned tube AHU steam coils.

## Tube-wall and overall UA
The model combines:
- air-side resistance
- fin efficiency effect
- fouling resistances
- tube wall conduction
- steam-side resistance

Overall heat transfer is combined on an outside-area basis for each row.

## Row-by-row solution
For each row, the app computes:
1. local air-side state
2. local steam-side state
3. air-side HTC and pressure drop
4. steam-side HTC and pressure drop
5. row UA
6. row duty
7. updated air leaving state
8. updated steam / condensate state

Because the reheat coil is dry, the air-side update is mainly:
- `Q_row = m_dot_air * cp_moist * (T_out - T_in)`
- `w_out = w_in`

## Pressure drop treatment

### Air side
Air-side pressure drop is accumulated row by row from the local velocity and friction estimate.

### Steam side
Steam-side pressure drop includes simplified internal flow and header contributions.

The pressure-drop model is suitable for engineering estimation, but final control-valve and trap selection should be based on a more detailed steam distribution review.

## Sizing mode
In sizing mode the app iterates on total steam flow until the target leaving dry bulb is met or until it becomes clear that the geometry is UA-limited.

This is useful for:
- estimating required steam flow
- checking whether rows are sufficient
- comparing two row counts or fin densities

## Major assumptions
1. Steady-state operation
2. Uniform air distribution across the face
3. Uniform steam distribution across circuits
4. Dry reheat only on air side
5. No moisture carryover from upstream coil
6. Negligible casing heat loss
7. Lumped row-by-row mean properties
8. No control valve dynamics
9. No condensate backing-up analysis
10. No steam trap dynamics

## Limitations
1. Not a full steam system design package
2. Does not model stall under modulating control
3. Does not model vacuum formation after shutoff
4. Does not model non-uniform coil flooding
5. Does not model freeze risk during low-load winter operation
6. Does not include detailed distributor / return bend geometry
7. Uses practical engineering correlations rather than full CFD or detailed two-phase network analysis

## Calibration philosophy
The app includes calibration placeholders for:
- air-side dp multiplier
- air-side heat-transfer multiplier
- steam single-phase heat-transfer multiplier
- steam condensation heat-transfer multiplier
- steam pressure-drop multiplier

These should be adjusted only after comparison to:
- supplier selection data
- laboratory coil tests
- stable AHU field measurements

## Expected accuracy
Before calibration, a practical expectation is:
- duty: within typical engineering selection range
- air-side pressure drop: approximate
- leaving air DB: generally more reliable than RH because humidity ratio is fixed by assumption
- steam pressure drop: screening-level unless validated

## References and engineering basis
The implementation is based on standard HVAC and heat-transfer practice, especially:
- ASHRAE Handbook Fundamentals for psychrometrics and heat transfer
- compact heat exchanger methods in the style of Kays and London
- classical internal forced convection and friction-factor relations
- practical in-tube condensation enhancement logic for engineering sizing

For project use, always compare against vendor software or measured data for the exact coil construction.
