# Credentials

Most models on the globe are open data and need nothing. Two sources
need a free account:

| Model | Credentials |
|---|---|
| UKV, UKMO Global | None (Met Office open data on AWS) |
| AROME 1.3 km, AROME 2.5 km, AROME-IFS | None (Météo-France open data) |
| ICON-EU | None (DWD open data) |
| ECMWF IFS 0.1 | None (ECMWF open data, CC BY 4.0: credit ECMWF) |
| GFS sflux, GFS 0.25 | None (NOAA open data on AWS) |
| HARMONIE (KNMI) | KNMI Open Data API key (works without one: see below) |
| IBI waves, NWS waves | Copernicus Marine account |

HARMONIE works out of the box with KNMI's shared anonymous key (below).
Without a Copernicus login, the two wave models' download buttons fail
with a message in the status bar; every other model works as normal.

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

**Getting started without a key:** if no personal key is set, the globe
uses KNMI's shared **anonymous key**, published on
https://developer.dataplatform.knmi.nl/open-data-api for unregistered
use (checked 18 November 2026). It is shared by everyone using it
(50 requests a minute, 3,000 an hour, across all anonymous users) and is
valid until **1 August 2027**; KNMI publishes the next one on the same
page. A HARMONIE fetch makes only two API requests (the 3.4 GB download
itself comes from a temporary link), so it fits, but a busy period for
other anonymous users can still make it fail. The status bar says when
the anonymous key is used. After it expires, copy the new key from that
page into `ANONYMOUS_KNMI_KEY` and `ANONYMOUS_KNMI_UNTIL` in
`globe_fetch.py`, or use a personal key.

## Copernicus Marine account (IBI and NWS waves)

1. Register at https://data.marine.copernicus.eu/register (free).
2. In an Anaconda Prompt, run once:

       conda run -n ukmo_globe copernicusmarine login

   and enter the Copernicus Marine username and password when asked.
3. The toolbox saves them in the user's profile:

       %USERPROFILE%\.copernicusmarine\.copernicusmarine-credentials

   (outside the project, so it is never committed). Nothing else is
   needed; the globe's wave downloads use this file.

Alternatives (environment variables read by the toolbox, checked in the
installed copernicusmarine 2.5.1):
- `COPERNICUSMARINE_SERVICE_USERNAME` and
  `COPERNICUSMARINE_SERVICE_PASSWORD` instead of the login file;
- `COPERNICUSMARINE_CREDENTIALS_DIRECTORY` to keep the login file in
  another folder.

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
