import sqlite3
import os
import time
import json
import hashlib

from uuid import uuid4
from datetime import datetime, timezone
from threading import Lock

from fastapi import FastAPI, Header, Query, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr


# Koya Clinic API

koya_api = FastAPI(title="Koya Clinic API")
koya_api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

koya_key = os.getenv("KOYA_API_KEY", "koya-assessment-key")
koya_database = "koya_clinic.db"

koya_lock = Lock()
koya_attempts = {}


# Database connection

def koya_connect():
    connection = sqlite3.connect(koya_database, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


# Create database tables

def koya_create_tables():
    connection = koya_connect()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS slots (
            id TEXT PRIMARY KEY,
            provider TEXT,
            start TEXT,
            end TEXT
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id TEXT PRIMARY KEY,
            slot_id TEXT,
            name TEXT,
            email TEXT,
            status TEXT,
            created_at TEXT
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
            request_hash TEXT,
            response TEXT
        )
    """)

    connection.commit()
    connection.close()


koya_create_tables()


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


# Check API key

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


# Reset appointment slots

@koya_api.post(
    "/reset",
    dependencies=[Depends(koya_check_key)]
)
def koya_reset(data: KoyaReset):

    connection = koya_connect()

    try:
        connection.execute("BEGIN IMMEDIATE")

        connection.execute("DELETE FROM bookings")
        connection.execute("DELETE FROM slots")
        connection.execute("DELETE FROM idempotency")

        for slot in data.slots:
            connection.execute("""
                INSERT INTO slots (id, provider, start, end)
                VALUES (?, ?, ?, ?)
            """, (
                slot.id,
                slot.provider,
                slot.start.isoformat(),
                slot.end.isoformat()
            ))

        connection.commit()
        return {"message": "Done"}

    except sqlite3.IntegrityError:
        connection.rollback()
        raise HTTPException(
            status_code=400,
            detail="Duplicate slot ID"
        )

    finally:
        connection.close()


# Display available slots

@koya_api.get(
    "/slots",
    dependencies=[Depends(koya_check_key)]
)
def koya_get_slots():

    connection = koya_connect()

    slots = connection.execute("""
        SELECT * FROM slots ORDER BY start
    """).fetchall()

    bookings = connection.execute("""
        SELECT slot_id FROM bookings
        WHERE status = 'confirmed'
    """).fetchall()

    booked_slots = [booking["slot_id"] for booking in bookings]

    connection.close()

    return {
        "slots": [
            {
                "id": slot["id"],
                "provider": slot["provider"],
                "start": slot["start"],
                "end": slot["end"],
                "available": slot["id"] not in booked_slots
            }
            for slot in slots
        ]
    }


# Limit booking attempts

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


# Book an appointment

@koya_api.post(
    "/bookings",
    status_code=201,
    dependencies=[Depends(koya_check_key)]
)
def koya_create_booking(
    data: KoyaBooking,
    idempotency_key: str = Header(None, alias="Idempotency-Key")
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

    connection = koya_connect()

    try:
        connection.execute("BEGIN IMMEDIATE")

        # Check repeated requests

        if idempotency_key:
            previous = connection.execute("""
                SELECT * FROM idempotency
                WHERE idempotency_key = ?
            """, (idempotency_key,)).fetchone()

            if previous:
                if previous["request_hash"] != request_hash:
                    raise HTTPException(
                        status_code=422,
                        detail="Idempotency key used with different details"
                    )

                connection.commit()
                return JSONResponse(
                    status_code=201,
                    content=json.loads(previous["response"])
                )

        # Check whether the slot exists

        slot = connection.execute("""
            SELECT * FROM slots WHERE id = ?
        """, (slot_id,)).fetchone()

        if not slot:
            raise HTTPException(
                status_code=404,
                detail="Slot not found"
            )

        # Check appointment date

        slot_date = datetime.fromisoformat(
            slot["start"].replace("Z", "+00:00")
        )

        if slot_date.tzinfo is None:
            slot_date = slot_date.replace(tzinfo=timezone.utc)

        if slot_date <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code=400,
                detail="Appointment slot has passed"
            )

        # Check if somebody has booked the slot

        existing = connection.execute("""
            SELECT id FROM bookings
            WHERE slot_id = ? AND status = 'confirmed'
        """, (slot_id,)).fetchone()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Slot already booked"
            )

        # Save the booking

        booking_id = str(uuid4())

        booking = {
            "id": booking_id,
            "slot_id": slot_id,
            "name": name,
            "email": email,
            "status": "confirmed",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        connection.execute("""
            INSERT INTO bookings
            (id, slot_id, name, email, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            booking["id"],
            booking["slot_id"],
            booking["name"],
            booking["email"],
            booking["status"],
            booking["created_at"]
        ))

        response = {"booking": booking}

        # Save the request key

        if idempotency_key:
            connection.execute("""
                INSERT INTO idempotency
                (idempotency_key, request_hash, response)
                VALUES (?, ?, ?)
            """, (
                idempotency_key,
                request_hash,
                json.dumps(response)
            ))

        connection.commit()

        return JSONResponse(
            status_code=201,
            content=response
        )

    except HTTPException:
        connection.rollback()
        raise

    finally:
        connection.close()


# Find one booking

@koya_api.get(
    "/bookings/{booking_id}",
    dependencies=[Depends(koya_check_key)]
)
def koya_find_booking(booking_id: str):

    connection = koya_connect()

    booking = connection.execute("""
        SELECT * FROM bookings WHERE id = ?
    """, (booking_id,)).fetchone()

    connection.close()

    if not booking:
        raise HTTPException(
            status_code=404,
            detail="Booking not found"
        )

    return {"booking": dict(booking)}


# Find a patient's bookings

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

    connection = koya_connect()

    total = connection.execute("""
        SELECT COUNT(*) AS total
        FROM bookings WHERE email = ?
    """, (email,)).fetchone()["total"]

    offset = (page - 1) * limit

    bookings = connection.execute("""
        SELECT * FROM bookings
        WHERE email = ?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """, (email, limit, offset)).fetchall()

    connection.close()

    return {
        "bookings": [dict(booking) for booking in bookings],
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

    connection = koya_connect()

    booking = connection.execute("""
        SELECT * FROM bookings WHERE id = ?
    """, (booking_id,)).fetchone()

    if not booking:
        connection.close()
        raise HTTPException(
            status_code=404,
            detail="Booking not found"
        )

    if booking["status"] == "cancelled":
        connection.close()
        raise HTTPException(
            status_code=400,
            detail="Booking already cancelled"
        )

    connection.execute("""
        UPDATE bookings
        SET status = 'cancelled'
        WHERE id = ?
    """, (booking_id,))

    connection.commit()
    connection.close()

    return {
        "message": "Booking cancelled",
        "status": "cancelled"
    }