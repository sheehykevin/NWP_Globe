# Credentials

Most models on the globe are open data and need nothing. Two sources
need a free account:

| Model | Credentials |
|---|---|
| UKV, UKMO Global | None (Met Office open data on AWS) |
| ICON-EU | None (DWD open data) |
| ECMWF IFS 0.1 | None (ECMWF open data, CC BY 4.0: credit ECMWF) |
| GFS sflux, GFS 0.25 | None (NOAA open data on AWS) |
| HARMONIE (KNMI) | KNMI Open Data API key |
| IBI waves, NWS waves | Copernicus Marine account |

Without them, those three models' download buttons fail with a message
in the status bar; every other model works as normal.

Credentials are personal: never commit them, email them or paste them
into issues. Each user gets their own.

## KNMI Open Data API key (HARMONIE)

1. Go to the KNMI Developer Portal, https://developer.dataplatform.knmi.nl,
   and sign up (free).
2. Request an API key for the **Open Data API** (not the EDR or WMS
   APIs). The key is a long string shown in your portal account.
3. Put it on the PC in **one** of these places:
   - A file named `.env` in the `NWP_Globe` folder (next to `globe.py`),
     containing one line:

         KNMI_API_KEY=your-key-here

     `.env` is listed in `.gitignore`, so git never commits it.
   - Or a Windows user environment variable `KNMI_API_KEY` (Start >
     "Edit environment variables for your account" > New). Restart the
     globe afterwards. The environment variable wins if both are set.

KNMI's portal also lists a shared anonymous key with low limits; a
personal key is better for the about 3.4 GB HARMONIE downloads.

## Copernicus Marine account (IBI and NWS waves)

1. Register at https://data.marine.copernicus.eu/register (free).
2. In an Anaconda Prompt, run once:

       conda run -n ukmo_globe copernicusmarine login

   and enter the Copernicus Marine username and password when asked.
3. The toolbox saves them in the user's profile:

       %USERPROFILE%\.copernicusmarine\.copernicusmarine-credentials

   (outside the project, so it is never committed). Nothing else is
   needed; the globe's wave downloads use this file.

Alternative, instead of the login file: set Windows user environment
variables `COPERNICUSMARINE_SERVICE_USERNAME` and
`COPERNICUSMARINE_SERVICE_PASSWORD`.

## Setting up another user's PC

1. Clone the repository and create the environment
   (`conda env create -f environment.yml`).
2. That user creates their own KNMI key and Copernicus account (above),
   puts the KNMI key in `NWP_Globe\.env` and runs
   `copernicusmarine login` once.
3. Start the globe and press a model's download button to check.

To remove credentials from a PC: delete `NWP_Globe\.env` (and any
`KNMI_API_KEY` environment variable) and the
`%USERPROFILE%\.copernicusmarine` folder.
