import os
import time
import json
import hashlib

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

from uuid import uuid4
from datetime import datetime, timezone
from threading import Lock

from fastapi import FastAPI, Header, Query, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr


# Koya Clinic API

load_dotenv()

koya_api = FastAPI(title="Koya Clinic API")

koya_api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

koya_key = os.getenv("KOYA_API_KEY", "koya-assessment-key")
database_url = os.getenv("DATABASE_URL")

koya_lock = Lock()
koya_attempts = {}


# Database connection

def koya_connect():
    if not database_url:
        raise RuntimeError("DATABASE_URL is missing")

    return psycopg.connect(
        database_url,
        row_factory=dict_row,
        connect_timeout=15
    )


# Create database tables

def koya_create_tables():

    with koya_connect() as connection:

        connection.execute("""
            CREATE TABLE IF NOT EXISTS slots (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                start TIMESTAMPTZ NOT NULL,
                "end" TIMESTAMPTZ NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id TEXT PRIMARY KEY,
                slot_id TEXT NOT NULL,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
        """)

        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS unique_slot_booking
            ON bookings(slot_id)
            WHERE status = 'confirmed'
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS idempotency (
                idempotency_key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                response TEXT NOT NULL
            )
        """)


# Request information

class KoyaSlot(BaseModel):
    id: str
    provider: str
    start: datetime
    end: datetime


class KoyaReset(BaseModel):
    slots: list[KoyaSlot]


class KoyaBooking(BaseModel):
    slot_id: str
    name: str
    email: EmailStr


# API key authentication

def koya_check_key(
    x_api_key: str = Header(None, alias="X-API-Key")
):

    if x_api_key != koya_key:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key"
        )


# Homepage

@koya_api.get("/")
def koya_home():

    return {
        "message": "Welcome to Koya Clinic API",
        "status": "running"
    }


# Reset slots

@koya_api.post(
    "/reset",
    dependencies=[Depends(koya_check_key)]
)
def koya_reset(data: KoyaReset):

    try:

        with koya_connect() as connection:

            connection.execute("DELETE FROM bookings")
            connection.execute("DELETE FROM slots")
            connection.execute("DELETE FROM idempotency")

            for slot in data.slots:

                if slot.end <= slot.start:
                    raise HTTPException(
                        status_code=400,
                        detail="Slot end must be after slot start"
                    )

                connection.execute("""
                    INSERT INTO slots (id, provider, start, "end")
                    VALUES (%s, %s, %s, %s)
                """, (
                    slot.id,
                    slot.provider,
                    slot.start,
                    slot.end
                ))

        return {"message": "Done"}

    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=400,
            detail="Duplicate slot ID"
        )


# View available slots

@koya_api.get(
    "/slots",
    dependencies=[Depends(koya_check_key)]
)
def koya_get_slots():

    with koya_connect() as connection:

        slots = connection.execute("""
            SELECT * FROM slots ORDER BY start
        """).fetchall()

        bookings = connection.execute("""
            SELECT slot_id FROM bookings
            WHERE status = 'confirmed'
        """).fetchall()

    booked_slots = [booking["slot_id"] for booking in bookings]

    return {
        "slots": [
            {
                "id": slot["id"],
                "provider": slot["provider"],
                "start": slot["start"].isoformat(),
                "end": slot["end"].isoformat(),
                "available": slot["id"] not in booked_slots
            }
            for slot in slots
        ]
    }


# Rate limiting

def koya_check_limit(email):

    current_time = time.time()

    with koya_lock:

        attempts = koya_attempts.get(email, [])

        attempts = [
            attempt for attempt in attempts
            if current_time - attempt < 60
        ]

        if len(attempts) >= 5:
            koya_attempts[email] = attempts
            return False

        attempts.append(current_time)
        koya_attempts[email] = attempts

    return True


# Create a booking

@koya_api.post(
    "/bookings",
    status_code=201,
    dependencies=[Depends(koya_check_key)]
)
def koya_create_booking(
    data: KoyaBooking,
    idempotency_key: str = Header(
        None,
        alias="Idempotency-Key"
    )
):

    name = data.name.strip()
    email = str(data.email).strip().lower()
    slot_id = data.slot_id.strip()

    if not name or not slot_id:
        raise HTTPException(
            status_code=400,
            detail="Missing booking information"
        )

    if not koya_check_limit(email):
        raise HTTPException(
            status_code=429,
            detail="Too many booking attempts"
        )

    booking_data = {
        "slot_id": slot_id,
        "name": name,
        "email": email
    }

    request_hash = hashlib.sha256(
        json.dumps(booking_data, sort_keys=True).encode()
    ).hexdigest()

    try:

        with koya_connect() as connection:

            connection.execute("BEGIN")

            if idempotency_key:

                previous = connection.execute("""
                    SELECT * FROM idempotency
                    WHERE idempotency_key = %s
                """, (idempotency_key,)).fetchone()

                if previous:

                    if previous["request_hash"] != request_hash:
                        raise HTTPException(
                            status_code=422,
                            detail="Idempotency key used with different details"
                        )

                    return JSONResponse(
                        status_code=201,
                        content=json.loads(previous["response"])
                    )

            slot = connection.execute("""
                SELECT * FROM slots WHERE id = %s
                FOR UPDATE
            """, (slot_id,)).fetchone()

            if not slot:
                raise HTTPException(
                    status_code=404,
                    detail="Slot not found"
                )

            if slot["start"] <= datetime.now(timezone.utc):
                raise HTTPException(
                    status_code=400,
                    detail="Appointment slot has passed"
                )

            existing = connection.execute("""
                SELECT id FROM bookings
                WHERE slot_id = %s AND status = 'confirmed'
            """, (slot_id,)).fetchone()

            if existing:
                raise HTTPException(
                    status_code=409,
                    detail="Slot already booked"
                )

            booking = {
                "id": str(uuid4()),
                "slot_id": slot_id,
                "name": name,
                "email": email,
                "status": "confirmed",
                "created_at": datetime.now(timezone.utc).isoformat()
            }

            connection.execute("""
                INSERT INTO bookings
                (id, slot_id, name, email, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                booking["id"],
                booking["slot_id"],
                booking["name"],
                booking["email"],
                booking["status"],
                booking["created_at"]
            ))

            response = {"booking": booking}

            if idempotency_key:

                connection.execute("""
                    INSERT INTO idempotency
                    (idempotency_key, request_hash, response)
                    VALUES (%s, %s, %s)
                """, (
                    idempotency_key,
                    request_hash,
                    json.dumps(response)
                ))

        return JSONResponse(
            status_code=201,
            content=response
        )

    except psycopg.errors.UniqueViolation:

        raise HTTPException(
            status_code=409,
            detail="Slot already booked"
        )


# Find one booking

@koya_api.get(
    "/bookings/{booking_id}",
    dependencies=[Depends(koya_check_key)]
)
def koya_find_booking(booking_id: str):

    with koya_connect() as connection:

        booking = connection.execute("""
            SELECT * FROM bookings WHERE id = %s
        """, (booking_id,)).fetchone()

    if not booking:
        raise HTTPException(
            status_code=404,
            detail="Booking not found"
        )

    return {"booking": booking}


# View patient bookings with pagination

@koya_api.get(
    "/bookings",
    dependencies=[Depends(koya_check_key)]
)
def koya_patient_bookings(
    email: str = Query(...),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100)
):

    email = email.strip().lower()

    with koya_connect() as connection:

        total = connection.execute("""
            SELECT COUNT(*) AS total
            FROM bookings WHERE email = %s
        """, (email,)).fetchone()["total"]

        offset = (page - 1) * limit

        bookings = connection.execute("""
            SELECT * FROM bookings
            WHERE email = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, (email, limit, offset)).fetchall()

    return {
        "bookings": bookings,
        "total": total,
        "page": page,
        "limit": limit
    }


# Cancel a booking

@koya_api.delete(
    "/bookings/{booking_id}",
    dependencies=[Depends(koya_check_key)]
)
def koya_cancel_booking(booking_id: str):

    with koya_connect() as connection:

        booking = connection.execute("""
            SELECT * FROM bookings WHERE id = %s
            FOR UPDATE
        """, (booking_id,)).fetchone()

        if not booking:
            raise HTTPException(
                status_code=404,
                detail="Booking not found"
            )

        if booking["status"] == "cancelled":
            raise HTTPException(
                status_code=400,
                detail="Booking already cancelled"
            )

        connection.execute("""
            UPDATE bookings
            SET status = 'cancelled'
            WHERE id = %s
        """, (booking_id,))

    return {
        "message": "Booking cancelled",
        "status": "cancelled"
    }


# Create database tables

koya_create_tables()