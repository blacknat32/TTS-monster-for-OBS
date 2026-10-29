const prompt = document.getElementById("id_message");
const counter = document.getElementById("message-count");

function updateCounter() {
    if (prompt && counter) {
        counter.textContent = prompt.value.length;
    }
}

document.querySelectorAll(".tag-button").forEach((button) => {
    button.addEventListener("click", () => {
        if (!prompt) {
            return;
        }

        const currentValue = prompt.value;
        const separator = currentValue && !/\s$/.test(currentValue) ? " " : "";
        prompt.value = `${currentValue}${separator}${button.dataset.tag} `;
        prompt.selectionStart = prompt.selectionEnd = prompt.value.length;
        prompt.focus();
        updateCounter();
    });
});

if (prompt) {
    prompt.addEventListener("input", updateCounter);
    updateCounter();
}

const soundSearch = document.getElementById("sound-search");
const soundEmpty = document.getElementById("sound-empty");
if (soundSearch) {
    soundSearch.addEventListener("input", () => {
        const query = soundSearch.value.trim().toLowerCase();
        let visibleCount = 0;
        document.querySelectorAll("#sound-list .chip").forEach((chip) => {
            const matches = chip.textContent.toLowerCase().includes(query);
            chip.hidden = !matches;
            if (matches) {
                visibleCount += 1;
            }
        });
        if (soundEmpty) {
            soundEmpty.hidden = visibleCount > 0;
        }
    });
}
