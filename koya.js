const API_URL = "http://127.0.0.1:8000";
const API_KEY = "koya-assessment-key";

const bookingForm = document.getElementById("bookingForm");
const confirmation = document.getElementById("confirmation");
const slotSelect = document.getElementById("slot");
const slotsContainer = document.getElementById("slots");
const bookButton = document.getElementById("bookButton");


// Load available appointment slots

async function loadSlots() {
    try {
        const response = await fetch(`${API_URL}/slots`, {
            headers: {
                "X-API-Key": API_KEY
            }
        });

        if (!response.ok) {
            throw new Error("Could not load appointment slots.");
        }

        const data = await response.json();

        slotSelect.innerHTML =
            '<option value="">Select an available slot</option>';

        slotsContainer.innerHTML = "";

        const availableSlots = data.slots.filter(slot => slot.available);

        if (availableSlots.length === 0) {
            slotsContainer.textContent = "No available appointments.";
            return;
        }

        availableSlots.forEach(slot => {
            const date = new Date(slot.start);

            const slotButton = document.createElement("button");
            slotButton.type = "button";
            slotButton.className = "slot";
            slotButton.textContent =
                `${slot.provider} - ${date.toLocaleString()}`;

            slotButton.addEventListener("click", () => {
                slotSelect.value = slot.id;
            });

            slotsContainer.appendChild(slotButton);

            const option = document.createElement("option");
            option.value = slot.id;
            option.textContent =
                `${slot.provider} - ${date.toLocaleString()}`;

            slotSelect.appendChild(option);
        });

    } catch (error) {
        slotsContainer.textContent = error.message;
    }
}


// Book an appointment

bookingForm.addEventListener("submit", async function(event) {
    event.preventDefault();

    const name = document.getElementById("name").value.trim();
    const email = document.getElementById("email").value.trim();
    const slotId = slotSelect.value;

    if (!slotId) {
        confirmation.textContent =
            "Please select an appointment slot.";
        return;
    }

    bookButton.disabled = true;
    bookButton.textContent = "Booking...";

    try {
        const response = await fetch(`${API_URL}/bookings`, {
            method: "POST",

            headers: {
                "Content-Type": "application/json",
                "X-API-Key": API_KEY,
                "Idempotency-Key": crypto.randomUUID()
            },

            body: JSON.stringify({
                slot_id: slotId,
                name: name,
                email: email
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Booking failed.");
        }

        const booking = data.booking;

        confirmation.textContent =
            `Appointment booked successfully! Booking ID: ${booking.id}`;

        bookingForm.reset();

        await loadSlots();

    } catch (error) {
        confirmation.textContent = error.message;

    } finally {
        bookButton.disabled = false;
        bookButton.textContent = "Book Appointment";
    }
});


// Start the website

loadSlots();