// Panneau de modération : rafraîchit la file toutes les 3 secondes sans
// recharger la page (les nouvelles demandes du chat apparaissent seules), et
// demande une confirmation avant les actions marquées data-confirm (Bannir).
// L'historique est repliable et redimensionnable : son état (ouvert ou non,
// hauteur, défilement) est relu avant chaque rafraîchissement puis réappliqué,
// et l'ouverture et la hauteur sont gardées d'une visite à l'autre.
const queue = document.getElementById("queue");
const REFRESH_MS = 3000;
const HISTORY_KEY = "moderation-history";

// Bouton de souris enfoncé dans la file (ex. glisser la poignée de
// l'historique) : on ne rafraîchit pas en plein geste.
let pointerDown = false;

function loadHistoryState() {
    try {
        return JSON.parse(localStorage.getItem(HISTORY_KEY)) || {};
    } catch (error) {
        return {};
    }
}

function saveHistoryState(state) {
    try {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(state));
    } catch (error) {
        // Stockage indisponible (navigation privée…) : l'état reste en mémoire.
    }
}

let historyState = loadHistoryState();

function readHistoryState() {
    const details = document.getElementById("history");
    if (!details) {
        return;
    }
    const list = details.querySelector(".history-scroll");
    historyState.open = details.open;
    if (list && details.open) {
        // La hauteur n'est notée que si l'utilisateur a tiré la poignée.
        if (list.style.height) {
            historyState.height = list.style.height;
        }
        historyState.scroll = list.scrollTop;
    }
    saveHistoryState({ open: historyState.open, height: historyState.height });
}

function applyHistoryState() {
    const details = document.getElementById("history");
    if (!details) {
        return;
    }
    if (typeof historyState.open === "boolean") {
        details.open = historyState.open;
    }
    const list = details.querySelector(".history-scroll");
    if (list) {
        if (historyState.height) {
            list.style.height = historyState.height;
        }
        list.scrollTop = historyState.scroll || 0;
    }
}

async function refreshQueue() {
    if (!queue || document.hidden || pointerDown) {
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
            const html = await response.text();
            if (pointerDown) {
                return;
            }
            readHistoryState();
            queue.innerHTML = html;
            applyHistoryState();
        }
    } catch (error) {
        // Serveur arrêté : on réessaiera au prochain tour.
    }
}

if (queue) {
    applyHistoryState();
    queue.addEventListener("pointerdown", () => { pointerDown = true; });
    window.addEventListener("pointerup", () => {
        pointerDown = false;
        readHistoryState();
    });
    // « toggle » ne remonte pas : on l'écoute en phase de capture.
    queue.addEventListener("toggle", readHistoryState, true);
    setInterval(refreshQueue, REFRESH_MS);
    queue.addEventListener("submit", (event) => {
        const message = event.target.dataset.confirm;
        if (message && !window.confirm(message)) {
            event.preventDefault();
        }
    });
}
