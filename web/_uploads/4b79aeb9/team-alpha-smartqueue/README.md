# SmartQueue

A real-time queue management system for outpatient clinics. Patients
check in via SMS and get a live position estimate instead of waiting in
a physical line.

## Problem

Clinic waiting rooms are overcrowded and patients have no visibility
into how long they'll wait, leading to frustration and missed calls
when it's their turn.

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Then visit http://localhost:5000

## Architecture

- Flask backend serving a REST API
- SQLite for queue state (swappable to Postgres via SQLALCHEMY_DATABASE_URI)
- Twilio webhook for SMS check-in
- Simple polling frontend (no websockets yet — known limitation)

## Known limitations

- No auth on the admin endpoint yet
- Queue position estimates assume fixed consultation time (5 min avg)
