document.querySelectorAll(".tag-button").forEach((button) => {
    button.addEventListener("click", () => {
        const prompt = document.getElementById("id_message");
        if (!prompt) {
            return;
        }

        const currentValue = prompt.value;
        const separator = currentValue && !/\s$/.test(currentValue) ? " " : " ";
        prompt.value = `${currentValue}${separator}${button.dataset.tag}`;
        prompt.selectionStart = prompt.selectionEnd = prompt.value.length;
        prompt.focus();
    });
});
