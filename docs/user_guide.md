# User Guide - Steam Air Heater Coil Designer

## 1. Purpose

This app models a steam reheat coil installed after a cooling coil in an AHU. Air entering the reheat coil can be close to saturation. The app reheats that air sensibly, so humidity ratio remains constant while dry bulb rises and relative humidity falls.

## 2. User roles

### Standard user
- log in with assigned username and password
- enter geometry and operating conditions
- run rating or sizing calculations
- save and reload their own design JSON files
- download summary and row-by-row results

### Admin user
- everything a standard user can do
- edit master calibration multipliers
- edit app settings
- download calibration and settings JSON backups

## 3. Login setup

Create users in Streamlit secrets. Streamlit reads secrets from `.streamlit/secrets.toml` locally or from the Cloud secrets manager when deployed.

Recommended steps:
1. run `python generate_password_hash.py "YourPassword"`
2. copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`
3. paste the generated hashes for each user
4. set `role = "admin"` only for administrators

## 4. Running the app

```bash
pip install -r requirements.txt
streamlit run steam_air_heater_coil_app.py
```

## 5. Inputs

### Coil geometry
- face width and face height
- row pitch and longitudinal pitch
- rows
- tube OD and wall thickness
- FPI and fin thickness
- fin and tube material
- circuits
- sigma free-flow area ratio

### Air side
- face velocity or volume flow
- inlet dry bulb + RH, or inlet dry bulb + wet bulb
- target leaving DB for sizing mode, or reference target for rating mode

### Steam side
- steam pressure in bar(g) or bar(abs)
- inlet state: saturated dry, wet steam, or superheated
- wet steam quality
- superheat
- condensate subcooling allowance
- header diameters and header length
- steam mass flow in rating mode

## 6. Saved designs

The sidebar lets each user:
- save the current inputs into their own saved-design folder
- load one of their own saved designs
- load a shared example file from the repo
- upload a JSON design and load it into the session
- delete one of their own saved designs

Each browser session keeps its own state, so multiple logged-in users can work on different cases simultaneously without sharing on-screen widget values.

## 7. Results

The Results tab shows:
- total duty
- leaving air DB, WB, RH, and dew point
- steam flow and condensate rate
- steam-side and air-side pressure drops
- rows needed to meet target
- row-by-row data table
- calibration factors applied during the run

## 8. Admin tab

### Calibration
Use this page to tune the model against measured or vendor data. Save writes the master calibration JSON and creates a backup.

### Settings
Use this page to change the visible app title/subtitle and the save-folder behavior.

## 9. Recommended workflow

1. log in
2. load an example or enter geometry manually
3. run a baseline calculation
4. save the design under a meaningful name
5. compare with vendor or test data
6. if you are admin, tune calibration multipliers and rerun
7. export the summary JSON and row table CSV
