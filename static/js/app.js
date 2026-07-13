function toggleTheme() {

    const random = document.querySelector(
        'input[value="random"]'
    ).checked;

    document.getElementById("themeSelect").disabled = random;
}

window.onload = toggleTheme;