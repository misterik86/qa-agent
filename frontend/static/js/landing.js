document.addEventListener("DOMContentLoaded", () => {
  // Copy buttons for templates
  document.querySelectorAll(".copy-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.target;
      const text = document.querySelector(target).value;
      navigator.clipboard.writeText(text).then(() => {
        btn.textContent = "Скопировано!";
        setTimeout(() => (btn.textContent = "Копировать"), 1500);
      });
    });
  });

  // Price calculator
  const calcTests = document.getElementById("calc-tests");
  const calcPar = document.getElementById("calc-par");
  const calcSso = document.getElementById("calc-sso");
  const testsVal = document.getElementById("calc-tests-val");
  const parVal = document.getElementById("calc-par-val");
  const priceEl = document.getElementById("calc-price");

  const updatePrice = () => {
    const tests = parseInt(calcTests.value, 10);
    const par = parseInt(calcPar.value, 10);
    const sso = calcSso.checked;
    testsVal.textContent = tests;
    parVal.textContent = par;
    if (sso) {
      priceEl.textContent = "15 000 ₽/мес (Enterprise)";
    } else if (tests <= 100 && par <= 5) {
      priceEl.textContent = "3 500 ₽/мес (Pro)";
    } else if (tests <= 100) {
      priceEl.textContent = "3 500 ₽/мес (Pro) — увеличите параллельность в Enterprise";
    } else {
      priceEl.textContent = "15 000 ₽/мес (Enterprise)";
    }
  };
  calcTests.addEventListener("input", updatePrice);
  calcPar.addEventListener("input", updatePrice);
  calcSso.addEventListener("change", updatePrice);
  updatePrice();
});