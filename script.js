document.addEventListener("DOMContentLoaded", function () {
    let telegram_id = "123456";  // Telegram orqali haqiqiy ID olish kerak

    document.getElementById("click-btn").addEventListener("click", function () {
        fetch("/click", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ telegram_id: telegram_id })
        })
        .then(response => response.json())
        .then(data => {
            document.getElementById("balance").textContent = data.balance;
        });
    });
});
