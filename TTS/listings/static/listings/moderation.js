// Panneau de modération : rafraîchit la file toutes les 3 secondes sans
// recharger la page (les nouvelles demandes du chat apparaissent seules), et
// demande une confirmation avant les actions marquées data-confirm (Bannir).
const queue = document.getElementById("queue");
const REFRESH_MS = 3000;

async function refreshQueue() {
    if (!queue || document.hidden) {
        return;
    }
    // On ne remplace pas la file pendant qu'un bouton de la file est survolé ou
    // sélectionné, pour ne pas perdre un clic.
    if (queue.matches(":hover") && queue.querySelector("button:hover, button:focus")) {
        return;
    }
    try {
        const response = await fetch(queue.dataset.url, { headers: { "X-Requested-With": "fetch" } });
        if (response.ok) {
            queue.innerHTML = await response.text();
        }
    } catch (error) {
        // Serveur arrêté : on réessaiera au prochain tour.
    }
}

if (queue) {
    setInterval(refreshQueue, REFRESH_MS);
    queue.addEventListener("submit", (event) => {
        const message = event.target.dataset.confirm;
        if (message && !window.confirm(message)) {
            event.preventDefault();
        }
    });
}
