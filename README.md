# Steam Air Heater Coil Designer

Streamlit app for rating and sizing an AHU steam reheat coil after a cooling coil, with the same general coil geometry style as your DX evaporator project.

## What is new in this version

- multi-user login with separate username/password accounts
- admin-only calibration and app-settings page
- per-user saved design files under a user-tagged folder
- load from saved JSON, upload JSON, or load repo examples
- same steam reheat thermal core as before, now with master calibration multipliers applied in the run

## Repository structure

```text
steam_air_heater_coil_repo/
├── .streamlit/
│   └── secrets.toml.example
├── calibration/
│   ├── backup/
│   ├── app_settings.json
│   └── calibration_data.json
├── docs/
│   ├── theory.md
│   └── user_guide.md
├── examples/
│   └── example_1_low_pressure_steam.json
├── test_data/
│   └── test_record_template.json
├── .gitignore
├── generate_password_hash.py
├── README.md
├── requirements.txt
└── steam_air_heater_coil_app.py
```

## Installation

```bash
pip install -r requirements.txt
streamlit run steam_air_heater_coil_app.py
```

## Authentication setup

This app uses a custom username/password gate stored in Streamlit secrets. Streamlit exposes secrets through `st.secrets`, and per-project secrets can be placed in `.streamlit/secrets.toml` for local runs. On Community Cloud, the same values go into the Secrets panel. `st.session_state` is isolated per user session, which is why each user can keep separate on-screen inputs and results.

### 1) Generate password hashes

Run this for each password:

```bash
python generate_password_hash.py "YourStrongPassword"
```

### 2) Create local secrets file

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and replace each `password_hash` with the generated hash.

Example structure:

```toml
[auth.users.admin]
password_hash = "pbkdf2_sha256$200000$..."
role = "admin"
enabled = true

[auth.users.engineer1]
password_hash = "pbkdf2_sha256$200000$..."
role = "user"
enabled = true
```

### 3) Streamlit Community Cloud

Open your deployed app settings and paste the same TOML into the Secrets section. If you later add or change secrets, restart the app so the updated values are picked up.

## How per-user saving works

- each logged-in user gets their own folder under `saved_designs/<username>/`
- users can save current inputs, reload their own saved files, delete their own saved files, or download the current design JSON
- example files in `/examples` are visible to all users
- on-screen results stay separate because the app uses session state rather than one shared in-memory design object.

## Admin controls

Admin users see an extra **Admin** tab.

### Calibration page

Admin can edit and save:
- air-side heat-transfer multiplier
- air-side pressure-drop multiplier
- steam-side heat-transfer multipliers for single-phase, condensing, and subcooled regions
- steam-side pressure-drop multipliers for single-phase, condensing, and headers

Saved edits update `calibration/calibration_data.json` and also write a timestamped backup file in `calibration/backup/`.

### Settings page

Admin can edit and save:
- app title
- app subtitle
- root folder name for saved designs
- whether users may upload JSON design files
- max saved designs per user

Saved edits update `calibration/app_settings.json` and also write a timestamped backup file.

## Engineering model scope

Included:
- rating mode and sizing mode
- row-by-row steam reheat coil simulation
- dry sensible reheating at constant humidity ratio
- superheated, wet, saturated, condensing, and optional subcooled condensate regions
- air-side fin / louver options and pressure-drop estimate
- header pressure-drop estimate
- calibration multipliers applied at run time

Not yet included:
- control valve sizing
- steam trap sizing
- stall / vacuum-breaker analysis
- condensate backup and freeze-protection logic
- true circuit-by-circuit steam maldistribution

## Built-in Streamlit auth note

Streamlit also has official OIDC-based authentication support, but this repo uses a custom in-app login because you asked for your own username/password list and admin/user roles inside the engineering app.
