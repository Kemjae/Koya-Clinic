Koya Clinic Appointment Booking System

Koya Clinic is a simple clinic appointment booking system built to make it easier for patients to view available appointment slots and book appointments online.

The project includes a web interface and a Python-based API that manages appointment bookings.

Features

* View available appointment slots.
* Book clinic appointments.
* Receive booking confirmations.
* Look up individual bookings.
* Cancel appointments.
* API key authentication.
* Prevent duplicate bookings for the same slot.
* Idempotency support for repeated booking requests.
* Pagination for patient booking records.
* Basic booking rate limiting.
* SQLite database storage.

Technologies Used

* HTML
* CSS
* JavaScript
* Python
* FastAPI
* SQLite
* Git and GitHub

Project Structure

Koya-Clinic/
│
├── Koya.html
├── Koya.css
├── Koya.js
├── README.md
├── .gitignore
│
└── backend/
    ├── Koya.py
    └── requirements.txt

Running the Project Locally

1. Clone the repository

git clone https://github.com/Kemjae/Koya-Clinic.git

2. Enter the project folder

cd Koya-Clinic

3. Enter the backend folder

cd backend

4. Create a virtual environment

python -m venv .venv

5. Activate the environment

Windows:

.venv\Scripts\activate

6. Install dependencies

pip install -r requirements.txt

7. Start the API

python -m uvicorn Koya:koya_api --reload

The API documentation will be available at:

http://127.0.0.1:8000/docs

API Endpoints

Method	Endpoint	Description
GET	/	API welcome page
POST	/reset	Reset appointment slots
GET	/slots	View available slots
POST	/bookings	Create a booking
GET	/bookings/{booking_id}	Find a booking
GET	/bookings	View patient bookings
DELETE	/bookings/{booking_id}	Cancel a booking

Authentication

Protected API endpoints use an API key supplied through the X-API-Key request header.

Project Purpose

This project was developed as a practical exercise in backend API development, database management, frontend integration and software deployment.

Developer

Chendo Chiemelie Nkemjika

GitHub: https://github.com/Kemjae/Koya-Clinic