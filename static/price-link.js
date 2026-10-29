// MRP, কমিশন % আর বিক্রয়মূল্যের ঘর একসাথে মিলিয়ে রাখে: MRP বা কমিশন বদলালে বিক্রয়মূল্য নিজে বসে,
// বিক্রয়মূল্য লিখলে কমিশন % নিজে বের হয়। বিক্রয়মূল্য পুরো টাকায় রাউন্ড হয়।
function linkPrices(mrpId, pctId, priceId) {
  const mrp = document.getElementById(mrpId), pct = document.getElementById(pctId), price = document.getElementById(priceId);
  if (!mrp || !pct || !price) return;
  let syncing = false;
  const set = (el, value) => {
    syncing = true;
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));  // পেজের নিজের হিসাব (যেমন লাভের হিসাব) আপডেট হোক
    syncing = false;
  };
  const fromPct = () => {
    if (syncing) return;
    const m = Number(mrp.value) || 0, p = Number(pct.value) || 0;
    if (m > 0 && pct.value !== '') set(price, Math.round(m * (1 - Math.min(100, Math.max(0, p)) / 100)));
  };
  mrp.addEventListener('input', fromPct);
  pct.addEventListener('input', fromPct);
  price.addEventListener('input', () => {
    if (syncing) return;
    const m = Number(mrp.value) || 0, s = Number(price.value) || 0;
    if (m > 0 && s > 0 && s <= m) set(pct, Math.round((1 - s / m) * 10000) / 100);
  });
}
