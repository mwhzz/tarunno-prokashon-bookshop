
let allBooks             = [];
let allCustomers         = [];
let lastCustomerFetch    = 0;
let cart                 = [];
let currentCustomerDue   = 0;
let lowStockFilterActive = false;
let savedRetailCommission = 0;
let phoneCheckTimer      = null;
let saleSubmitting = false;
let catalogLoading = true;
let dueLookupId = 0;
let feedbackTimer;
let draftReady = false;
let customersLoading = false;
let returnSubmitting = false;

const CART_STORAGE_KEY = 'pos_cart_draft_v1';
const PLACEHOLDER_COVER = 'data:image/svg+xml;utf8,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="158" height="130"><rect width="100%" height="100%" fill="#eafaf2"/><g transform="translate(79,65)" fill="none" stroke="#1a6347" stroke-width="3"><rect x="-26" y="-20" width="52" height="40" rx="3"/><line x1="0" y1="-20" x2="0" y2="20"/></g></svg>'
);

/* warn before an accidental refresh/close wipes an in-progress sale */
window.addEventListener('beforeunload', function (e) {
  if (cart.length) {
    e.preventDefault();
    e.returnValue = '';
  }
});

/* ৳ amounts everywhere in POS: whole taka, Bengali numerals — matches rest of the app */
function money(v) {
  // অন্য সব পেজে ব্যবহৃত num()-এর সাথে মিলিয়ে রাখা হয়েছে, নাহলে একই বিক্রয়ের টাকার
  // অঙ্ক POS-এ এক রকম আর ইনভয়েস/ড্যাশবোর্ডে অন্য রকম (রাউন্ড করা) দেখাতো।
  return new Intl.NumberFormat('bn-BD', {maximumFractionDigits:2}).format(Number(v) || 0);
}

function coverFallback(img) {
  img.onerror = null;
  img.src = PLACEHOLDER_COVER;
}

/* ══════════════════════════════════════
   CART DRAFT PERSISTENCE (survive refresh)
══════════════════════════════════════ */
const DRAFT_FIELDS = ['sale-type','commission','discount','pack','courier','txfee','cus-name','cus-phone','pay-method','paid'];
function saveCartDraft() {
  if (!draftReady || !document.getElementById('sale-type')) return;
  const fields = Object.fromEntries(DRAFT_FIELDS.map(id => [id,document.getElementById(id)?.value || '']));
  try { localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({cart,fields,savedRetailCommission})); }
  catch (_) { setText('draft-status','খসড়া রাখা যায়নি। পেজ বন্ধ করার আগে বিল শেষ করুন।'); }
}
function clearCartDraft() { try {localStorage.removeItem(CART_STORAGE_KEY);} catch (_) {} }
function loadCartDraft() {
  try {
    const raw = localStorage.getItem(CART_STORAGE_KEY); if (!raw) return;
    const saved = JSON.parse(raw), items = Array.isArray(saved) ? saved : saved.cart;
    if (!Array.isArray(items) || !items.length) return;
    cart = items.flatMap(item => {
      const book = allBooks.find(b => b.id === item.bid);
      if (!book || !book.is_active) return [];
      const qty = Math.min(Number(book.shop_quantity)||0,Math.max(0,Math.floor(Number(item.qty)||0)));
      if (!qty) return [];
      return [{bid:book.id,title:book.title,price:Number(book.mrp)||Number(book.selling_price)||0,qty,max:Number(book.shop_quantity)||0,defaultCommission:Number(book.commission)||0,extraCommission:Math.min(100,Math.max(0,Number(item.extraCommission)||0))}];
    });
    if (cart.length && saved.fields) {
      DRAFT_FIELDS.forEach(id => { if (saved.fields[id] !== undefined) document.getElementById(id).value = saved.fields[id]; });
      savedRetailCommission = Number(saved.savedRetailCommission)||0;
      document.getElementById('ws-section').style.display = saved.fields['sale-type'] === 'wholesale' ? 'block' : 'none';
      onPayMethodChange();
    }
    if (cart.length) setText('draft-status','আগের অসম্পূর্ণ বিল ফিরে এসেছে। বর্তমান দাম ও স্টক যাচাই করুন।');
  } catch (_) { clearCartDraft(); }
}

/* ══════════════════════════════════════
   ERROR FORMATTING
══════════════════════════════════════ */
function formatApiError(e) {
  if (!e || typeof e !== 'object') return 'একটি ত্রুটি হয়েছে। আবার চেষ্টা করুন।';
  if (e.detail) return String(e.detail);
  const parts = [];
  for (const key in e) {
    const val = e[key];
    parts.push(Array.isArray(val) ? val.join(' ') : String(val));
  }
  return parts.length ? parts.join(' | ') : 'একটি ত্রুটি হয়েছে। আবার চেষ্টা করুন।';
}

/* ══════════════════════════════════════
   INIT
══════════════════════════════════════ */
async function loadAllBooks() {
  let page = 1;
  let books = [];
  while (true) {
    const r = await api(`/books/?page=${page}&page_size=100`);
    books = books.concat(r.results);
    if (!r.next) break;
    page++;
  }
  return books;
}

async function initPOS() {
  const root = document.getElementById('pos-root');
  catalogLoading = true;
  draftReady = false;
  // Finish loading before draft restore, so a book on a later page is never lost.
  try {
    allBooks = await loadAllBooks();
    renderPOS(root);
    loadCartDraft();
    draftReady = true;
    catalogLoading = false;
    filterBooks(); renderCart(); calcTotals();
    document.getElementById('book-search').focus();
    refreshCustomersIfStale();
    if (document.getElementById('cus-phone').value) fetchDue();
  } catch (e) {
    root.innerHTML = `<div class="pos-loading" role="alert"><h2>বইয়ের তালিকা লোড হয়নি</h2><p>সংযোগ চেক করে আবার চেষ্টা করুন। আগের খসড়া রাখা আছে।</p><button class="pos-button secondary" onclick="initPOS()">আবার চেষ্টা করুন</button></div>`;
  }
}

function renderPOS(container) {
  container.innerHTML = `
  <div class="pos-shell">
    <section class="books-panel" aria-labelledby="picker-title">
      <div class="panel-heading"><div><span class="section-kicker">ধাপ ১</span><h2 id="picker-title">বই বাছাই করুন</h2></div><span id="catalog-count" class="count-pill"></span></div>
      <div class="picker-toolbar"><label class="search-label" for="book-search">বই খুঁজুন</label><div class="pos-search"><span>${icon('search',20)}</span><input id="book-search" placeholder="বইয়ের নাম, লেখক, ISBN বা কোড…" oninput="filterBooks()" onkeydown="handleSearchKey(event)" autocomplete="off"></div><p class="search-tip">বারকোড স্ক্যান করে Enter চাপুন · তালিকা বাছতে ↓ ব্যবহার করুন</p>
      <div class="pos-filter-row"><label>বিভাগ<select id="filter-group" onchange="filterBooks()"><option value="">সব বিভাগ</option></select></label><label>সাজান<select id="filter-sort" onchange="filterBooks()"><option value="name">নাম অনুযায়ী</option><option value="stock-asc">কম স্টক আগে</option><option value="price-asc">দাম কম থেকে বেশি</option><option value="price-desc">দাম বেশি থেকে কম</option></select></label><label class="filter-check"><input type="checkbox" id="filter-instock" checked onchange="filterBooks()">স্টকে আছে</label><button type="button" class="text-button" onclick="resetFilters()">সব বই দেখুন</button></div></div>
      <div class="catalog-caption"><span id="result-count" role="status" aria-live="polite"></span><span>স্বাভাবিক ছাড়সহ প্রতি কপি</span></div>
      <div class="books-scroll"><div id="book-grid" class="book-grid" onkeydown="handleGridKey(event)"></div></div>
    </section>
    <aside class="order-panel" aria-label="চলতি বিক্রয়">
      <div class="order-progress"><button type="button" id="cart-step" class="active" onclick="showCartStep()">১ · বই ও কার্ট</button><span>→</span><button type="button" id="payment-step" onclick="goToPayment()">২ · পেমেন্ট</button></div>
      <div id="checkout-error" role="alert" tabindex="-1" hidden></div>
      <div id="cart-stage">
        <div class="panel-heading"><div><h2>চলতি বিল</h2><span id="cart-count-lbl" class="muted"></span></div><button type="button" class="text-button danger" onclick="clearCart()">কার্ট খালি করুন</button></div>
        <div class="sale-mode"><label for="sale-type">বিক্রয়ের ধরন</label><select id="sale-type" onchange="toggleSaleType()"><option value="retail">খুচরা বিক্রয়</option><option value="wholesale">পাইকারি বিক্রয়</option></select></div>
        <div class="cart-table-wrap"><table class="cart-table"><thead><tr><th>বই ও পরিমাণ</th><th>মোট</th><th><span class="sr-only">মুছুন</span></th></tr></thead><tbody id="cart-scroll"></tbody></table></div>
        <details class="order-options"><summary>পুরো বিলের ছাড় ও অতিরিক্ত চার্জ <span id="adjustments-indicator"></span></summary><div class="options-body"><p class="discount-explanation" id="discount-explanation"></p><div class="bf-row"><div class="bf-field"><label for="commission">ছাড় / কমিশন (%)</label><input id="commission" type="number" value="0" min="0" max="100" step="any" oninput="fromCommission()"></div><div class="bf-field"><label for="discount">সমপরিমাণ ছাড় (৳)</label><input id="discount" type="number" value="0" min="0" step="0.01" oninput="calcTotals(true)"></div></div><p class="field-help">শতাংশ অথবা টাকা—একটি লিখলেই অন্যটি হিসাব হবে।</p><div class="bf-row charges"><div class="bf-field"><label for="pack">প্যাকিং (৳)</label><input id="pack" type="number" value="0" min="0" step="0.01" oninput="calcTotals()"></div><div class="bf-field"><label for="courier">কুরিয়ার (৳)</label><input id="courier" type="number" value="0" min="0" step="0.01" oninput="calcTotals()"></div><div class="bf-field"><label for="txfee">MFS ফি (৳)</label><input id="txfee" type="number" value="0" min="0" step="0.01" oninput="calcTotals()"></div></div></div></details>
        <div class="cart-footer"><div class="cart-footer-total"><span><b id="t-qty">০</b> কপি · <b id="t-items">০</b> ধরনের বই</span><strong id="cart-total">৳০</strong></div><button type="button" id="checkout-btn" class="pos-button primary wide" onclick="goToPayment()" disabled>পেমেন্টে যান →</button><p class="draft-status" id="draft-status" role="status">কার্টের খসড়া এই ব্রাউজারে রাখা হয়</p></div>
      </div>
      <div id="payment-stage" hidden>
        <div class="panel-heading"><div><span class="section-kicker">ধাপ ২</span><h2>ক্রেতা ও পেমেন্ট</h2></div><button type="button" class="text-button" onclick="showCartStep()">← কার্টে ফিরুন</button></div>
        <div class="checkout-body">
          <div id="ws-section" style="display:none"><div class="ws-wrap bf-field"><label for="wholesale-search">পাইকারি গ্রাহক খুঁজুন</label><input id="wholesale-search" placeholder="নাম, ফোন বা গ্রাহক কোড" oninput="searchWS()" autocomplete="off"><div id="wholesale-results"></div><input type="hidden" id="selected-ws-id"></div><p class="field-help">নতুন গ্রাহক হলে নিচে নাম ও ফোন লিখুন।</p></div>
          <div class="bf-row"><div class="bf-field"><label for="cus-name">ক্রেতার নাম</label><input id="cus-name" maxlength="200" placeholder="সাধারণ ক্রেতা" oninput="saveCartDraft()"></div><div class="bf-field"><label for="cus-phone">ফোন নম্বর</label><input id="cus-phone" type="tel" maxlength="20" placeholder="01XXXXXXXXX" oninput="onPhoneInput();saveCartDraft()" onfocus="refreshCustomersIfStale()"><small id="phone-check-indicator" role="status"></small></div></div><p class="field-help">নগদ বিক্রয়ে ঐচ্ছিক। বাকি রাখলে ক্রেতার নাম ও ফোন দিন।</p>
          <div class="totals-box"><div class="total-line"><span>বইয়ের মোট দাম</span><span id="t-sub"></span></div><div class="total-line"><span>মোট ছাড়</span><span id="t-disc"></span></div><div class="total-line"><span>অতিরিক্ত চার্জ</span><span id="t-extra"></span></div><div class="total-line big"><span>মোট বিল</span><strong id="t-total"></strong></div><div id="prev-due-row" class="total-line previous-due" style="display:none"><span>ক্রেতার আগের বাকি</span><span id="t-prev-due"></span></div></div>
          <div class="bf-field payment-method"><label for="pay-method">টাকা গ্রহণের মাধ্যম</label><select id="pay-method" onchange="onPayMethodChange()"><option value="cash">নগদ টাকা</option><option value="mobile">বিকাশ / নগদ</option><option value="bank">ব্যাংক</option><option value="credit">সম্পূর্ণ বাকি</option></select></div>
          <div class="paid-wrap"><label for="paid">ক্রেতা কত টাকা দিয়েছেন? (৳)</label><input id="paid" type="number" value="0" min="0" step="0.01" oninput="onPaidInput()"><div id="paid-quick-row"><button type="button" class="pos-button secondary" onclick="setPaidFull()">পুরো বিল পেয়েছি</button><button type="button" class="text-button" onclick="setPaidAmount(0)">পাইনি / বাকি</button></div><div class="paid-warn" id="paid-zero-warn" style="display:none">কোনো টাকা দেওয়া হয়নি। পুরো বিল বাকি থাকবে।</div><div class="paid-warn" id="paid-overpay-warn" style="display:none">দেওয়া টাকার অঙ্ক বিলের তুলনায় অনেক বেশি। আবার দেখুন।</div></div>
          <div class="payment-result"><div class="total-line"><span>এই বিলে বাকি</span><strong id="t-due"></strong></div><div class="total-line" id="change-row" style="display:none"><span>ক্রেতাকে ফেরত দিন</span><strong id="t-change"></strong></div><div class="total-line" id="grand-due-row" hidden><span>আগেরসহ মোট বাকি</span><strong id="t-grand-due"></strong></div></div>

          <button type="button" id="complete-btn" class="pos-button primary wide" onclick="completeSale()" disabled>বিল তৈরি করুন</button><p class="checkout-help">এই বোতাম চাপলে বিক্রয় সেভ হবে ও দোকানের স্টক কমবে।</p>
        </div>
      </div>
    </aside>
  </div><button type="button" id="mobile-cart" class="mobile-cart" onclick="document.querySelector('.order-panel').scrollIntoView({behavior:'smooth',block:'start'})">কার্ট দেখুন</button><div id="cart-feedback" class="cart-feedback" role="status" aria-live="polite"></div>`;
  updateGroupOptions(); filterBooks(); renderCart(); calcTotals();
}

function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function showCartStep() {
  if (saleSubmitting) return;
  document.getElementById('cart-stage').hidden = false;
  document.getElementById('payment-stage').hidden = true;
  document.getElementById('cart-step').classList.add('active');
  document.getElementById('payment-step').classList.remove('active');
}
function goToPayment() {
  if (!cart.length || saleSubmitting) return;
  document.getElementById('cart-stage').hidden = true;
  document.getElementById('payment-stage').hidden = false;
  document.getElementById('cart-step').classList.remove('active');
  document.getElementById('payment-step').classList.add('active');
  calcTotals(); document.getElementById('pay-method').focus();
  document.querySelector('.order-panel').scrollIntoView({behavior:'smooth',block:'start'});
}

function updateGroupOptions() {
  const sel = document.getElementById('filter-group');
  if (!sel) return;
  const current = sel.value;
  const groups = Array.from(new Set(allBooks.map(b => b.group_name).filter(Boolean))).sort((a,b) => a.localeCompare(b, 'bn'));
  sel.innerHTML = `<option value="">সব বিভাগ</option>` + groups.map(g => `<option value="${escapeHTML(g)}">${escapeHTML(g)}</option>`).join('');
  if (groups.includes(current)) sel.value = current;
}

function resetFilters() {
  lowStockFilterActive = false;
  const s   = document.getElementById('book-search');    if (s)   s.value = '';
  const g   = document.getElementById('filter-group');   if (g)   g.value = '';
  const so  = document.getElementById('filter-sort');    if (so)  so.value = 'name';
  const ins = document.getElementById('filter-instock'); if (ins) ins.checked = false;
  filterBooks();
}

/* ══════════════════════════════════════
   BOOK GRID
══════════════════════════════════════ */
function getFilteredBooks() {
  const q       = (document.getElementById('book-search')?.value || '').toLowerCase().trim();
  const group   = document.getElementById('filter-group')?.value || '';
  const sort    = document.getElementById('filter-sort')?.value || 'name';
  const inStock = document.getElementById('filter-instock')?.checked || false;

  let list = allBooks.filter(b =>
    b.is_active &&
    (!q ||
      b.title.toLowerCase().includes(q) ||
      (b.author||'').toLowerCase().includes(q) ||
      (b.product_code||'').toLowerCase().includes(q) ||
      (b.isbn||'').toLowerCase().includes(q)
    ) &&
    (!group || b.group_name === group) &&
    (!inStock || Number(b.shop_quantity||0) > 0) &&
    (!lowStockFilterActive || Number(b.shop_quantity||0) <= 5)
  );

  switch (sort) {
    case 'stock-asc':  list = list.sort((a,b) => Number(a.shop_quantity||0) - Number(b.shop_quantity||0)); break;
    case 'price-desc': list = list.sort((a,b) => (parseFloat(b.mrp)||parseFloat(b.selling_price)||0) - (parseFloat(a.mrp)||parseFloat(a.selling_price)||0)); break;
    case 'price-asc':  list = list.sort((a,b) => (parseFloat(a.mrp)||parseFloat(a.selling_price)||0) - (parseFloat(b.mrp)||parseFloat(b.selling_price)||0)); break;
    default:            list = list.sort((a,b) => a.title.localeCompare(b.title, 'bn')); break;
  }
  return list;
}

function handleSearchKey(e) {
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    document.querySelector('#book-grid .book-row')?.focus();
    return;
  }
  if (e.key !== 'Enter') return;
  e.preventDefault();
  const matches = getFilteredBooks();
  if (matches.length === 1) {
    addToCart(matches[0].id);
    const s = document.getElementById('book-search');
    if (s) { s.value = ''; filterBooks(); s.focus(); }
  } else if (matches.length > 1) {
    // একাধিক বই মিললে তীর-চিহ্ন (↓) দিয়ে লিস্ট থেকে বেছে নেওয়া যাবে, মাউস ছাড়াই
    document.querySelector('#book-grid .book-row')?.focus();
  } else {
    toast('কোনো বই পাওয়া যায়নি', 'error');
  }
}

function handleGridKey(e) {
  const row = e.target.closest('.book-row');
  if (!row) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const rows = Array.from(document.querySelectorAll('#book-grid .book-row'));
    const idx = rows.indexOf(row);
    const next = e.key === 'ArrowDown' ? rows[idx + 1] : rows[idx - 1];
    if (next) next.focus();
    else if (e.key === 'ArrowUp') document.getElementById('book-search')?.focus();
  } else if (e.key === 'Escape') {
    document.getElementById('book-search')?.focus();
  }
}

function filterBooks() {
  const list = getFilteredBooks();
  const grid = document.getElementById('book-grid');
  if (!grid) return;
  setText('catalog-count', money(allBooks.filter(b => b.is_active).length) + 'টি বই');
  setText('result-count', catalogLoading ? 'বাকি বই লোড হচ্ছে…' : money(list.length) + 'টি বই পাওয়া গেছে');
  if (!list.length) {
    grid.innerHTML = `<div class="empty-state"><h3>এই ফিল্টারে বই পাওয়া যায়নি</h3><p>নাম বা কোড বদলে দেখুন। স্টকহীন বই দেখতে ফিল্টার সরান।</p><button type="button" class="pos-button secondary" onclick="resetFilters()">সব বই দেখুন</button></div>`;
    return;
  }
  grid.innerHTML = list.map(b => {
    const mrp = Number(b.mrp) || 0, commission = Math.min(100,Math.max(0,Number(b.commission)||0));
    const net = mrp > 0 ? mrp * (1-commission/100) : Number(b.selling_price)||0;
    const quantity = Number(b.shop_quantity)||0, out = quantity <= 0;
    const selected = cart.find(i => i.bid === b.id);
    return `<div class="book-row ${out?'out':''} ${selected?'selected':''}" role="button" tabindex="${out?-1:0}" aria-disabled="${out}" aria-label="${escapeHTML(b.title)} — ${out?'স্টক নেই':money(net)+' টাকা, কার্টে যোগ করুন'}" onclick="addToCart(${b.id})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();addToCart(${b.id})}">
      <img class="book-cover" src="${escapeHTML(b.image || PLACEHOLDER_COVER)}" alt="" loading="lazy" onerror="coverFallback(this)">
      <div class="book-row-info"><div class="book-title">${escapeHTML(b.title)}</div><div class="book-author">${escapeHTML(b.author||'লেখক দেওয়া নেই')}</div><div class="book-meta"><span class="${out?'unavailable':''}">${out?'দোকানে স্টক নেই':'দোকানে '+money(quantity)+' কপি'}</span>${b.product_code?'<span>কোড '+escapeHTML(b.product_code)+'</span>':''}${selected?'<span>কার্টে '+money(selected.qty)+' কপি</span>':''}</div></div>
      <div class="book-price-block">${mrp>0&&commission>0?'<span class="book-price-mrp">৳'+money(mrp)+'</span>':''}<span class="book-price">৳${money(net)}</span></div><div class="book-row-add" aria-hidden="true">${out?'—':icon('plus',17)}</div></div>`;
  }).join('');
}

function addToCart(id) {
  const book = allBooks.find(b => b.id === id);
  if (saleSubmitting || !book || book.shop_quantity <= 0 || !book.is_active) return;

  const ex = cart.find(i => i.bid === id);
  if (ex) {
    if (ex.qty >= book.shop_quantity) { toast(`দোকানে মাত্র ${book.shop_quantity}টি আছে`, 'error'); return; }
    ex.qty++;
  } else {
    cart.push({
      bid: id,
      title: book.title,
      price: parseFloat(book.mrp) || parseFloat(book.selling_price) || 0,
      qty: 1,
      max: book.shop_quantity,
      defaultCommission: parseFloat(book.commission || 0),
      extraCommission: 0
    });
  }
  renderCart();
  calcTotals();
  const feedback = document.getElementById('cart-feedback');
  feedback.textContent = book.title + ' — কার্টে যোগ হয়েছে';
  clearTimeout(feedbackTimer); feedbackTimer = setTimeout(() => { feedback.textContent = ''; }, 1800);
}

function changeQty(id, d) {
  if (saleSubmitting) return;
  const item = cart.find(i => i.bid === id);
  if (!item) return;
  if (d > 0 && item.qty >= item.max) { toast(`দোকানে মাত্র ${item.max}টি আছে`, 'error'); return; }
  item.qty = Math.max(0, Math.min(item.max, item.qty + d));
  if (item.qty === 0) cart = cart.filter(i => i.bid !== id);
  renderCart();
  calcTotals();
}

function setQty(id, value) {
  if (saleSubmitting) return;
  const item = cart.find(i => i.bid === id);
  if (!item) return;
  let v = Math.floor(parseFloat(value) || 0);
  v = Math.max(0, Math.min(item.max, v));
  if (v === 0) cart = cart.filter(i => i.bid !== id);
  else item.qty = v;
  renderCart();
  calcTotals();
}

function removeItem(id) {
  if (saleSubmitting) return;
  cart = cart.filter(i => i.bid !== id);
  if (!cart.length) resetGlobalDiscount();
  renderCart();
  calcTotals();
}

function resetGlobalDiscount() {
  // কার্ট খালি হয়ে গেলে আগের বিক্রয়ের ম্যানুয়াল ছাড়/কমিশন% যেন পরের নতুন
  // বিক্রয়ে না লেগে যায় — নাহলে আগের বইয়ে দেওয়া বাড়তি ছাড় পরের অন্য বইয়ের
  // দামেও নীরবে যোগ হয়ে যেত।
  if (document.getElementById('commission')) document.getElementById('commission').value = 0;
  if (document.getElementById('discount'))    document.getElementById('discount').value = 0;
}

function setExtraCommission(id, value) {
  if (saleSubmitting) return;
  const item = cart.find(i => i.bid === id);
  if (!item) return;
  item.extraCommission = Math.min(100, Math.max(0, parseFloat(value || 0)));
  calcTotals();
}

async function clearCart() {
  if (!cart.length || saleSubmitting) return;
  const ok = await confirmModal('কার্টের সব বই মুছে ফেলতে চান? এই কাজটি বাতিল করা যাবে না।', { danger: true });
  if (!ok) return;
  cart = [];
  clearCartDraft();
  resetGlobalDiscount();
  renderCart();
  calcTotals();
}

function renderCart() {
  const el = document.getElementById('cart-scroll'); if (!el) return;
  setText('cart-count-lbl',cart.length ? money(cart.length)+' ধরনের বই যোগ হয়েছে' : 'বই যোগ করে বিল শুরু করুন');
  if (!cart.length) {
    el.innerHTML = `<tr><td colspan="3" class="cart-empty"><div class="cart-empty-icon">${icon('clipboard',28)}</div><strong>আপনার কার্ট খালি</strong><p>বইয়ের তালিকা থেকে পছন্দের বই যোগ করুন।</p></td></tr>`;
  } else {
    el.innerHTML = cart.map(i => `<tr><td><div class="ci-name"><strong>${escapeHTML(i.title)}</strong><small>প্রতি কপি ৳${money(i.price)} · মোট ছাড় ${money(itemCommissionPct(i))}%</small></div><div class="ci-qty"><button type="button" class="qb" aria-label="${escapeHTML(i.title)} এক কপি কমান" onclick="changeQty(${i.bid},-1)">−</button><input class="ci-count-input" aria-label="${escapeHTML(i.title)} পরিমাণ" type="number" min="1" max="${i.max}" value="${i.qty}" onchange="setQty(${i.bid},this.value)"><button type="button" class="qb" aria-label="এক কপি বাড়ান" ${i.qty>=i.max?'disabled':''} onclick="changeQty(${i.bid},1)">+</button></div><details class="ci-adjust" ${i.extraCommission>0?'open':''}><summary>এই বইয়ে অতিরিক্ত ছাড়${i.extraCommission>0?' '+money(i.extraCommission)+'%':''}</summary><label for="extra-${i.bid}">অতিরিক্ত (%)</label> <input id="extra-${i.bid}" type="number" min="0" max="100" step="0.01" value="${i.extraCommission||0}" onchange="setExtraCommission(${i.bid},this.value);renderCart()"></details></td><td><div class="ci-total">৳${money(itemNet(i))}</div></td><td><button type="button" class="ci-remove" aria-label="${escapeHTML(i.title)} মুছুন" onclick="removeItem(${i.bid})">${icon('close',17)}</button></td></tr>`).join('');
  }
  filterBooks(); saveCartDraft();
}

function toggleSaleType() {
  const wholesale = document.getElementById('sale-type').value === 'wholesale';
  const commission = document.getElementById('commission');
  document.getElementById('ws-section').style.display = wholesale ? 'block' : 'none';
  document.getElementById('selected-ws-id').value = '';
  document.getElementById('wholesale-search').value = '';
  document.getElementById('wholesale-results').style.display = 'none';
  if (wholesale) { savedRetailCommission = Number(commission.value)||0; commission.value = 0; }
  else commission.value = savedRetailCommission || 0;
  fromCommission();
}

function searchWS() {
  const q   = document.getElementById('wholesale-search').value.toLowerCase();
  const box = document.getElementById('wholesale-results');
  if (!q) { box.style.display = 'none'; return; }

  const m = allCustomers.filter(c =>
    c.customer_type === 'wholesale' &&
    (c.name.toLowerCase().includes(q) || (c.phone||'').includes(q) || (c.customer_code||'').toLowerCase().includes(q))
  );

  box.innerHTML = m.length
    ? m.map(c => `
        <button type="button" class="wsr-item" onclick="selectWS(${c.id})">
          <strong>${escapeHTML(c.name)}</strong>
          <small>কোড: ${escapeHTML(c.customer_code||'—')} | ফোন: ${escapeHTML(c.phone)}</small>
        </button>`).join('')
    : `<div class="wsr-item"><small>কোনো গ্রাহক পাওয়া যায়নি</small></div>`;

  box.style.display = 'block';
}

function selectWS(id) {
  const c = allCustomers.find(x => x.id === id);
  if (!c) return;
  document.getElementById('selected-ws-id').value   = c.id;
  document.getElementById('wholesale-search').value = `${c.name} (${c.customer_code})`;
  document.getElementById('wholesale-results').style.display = 'none';
  document.getElementById('cus-name').value  = c.name;
  document.getElementById('cus-phone').value = c.phone;
  document.getElementById('commission').value = c.default_commission;
  fetchDue(); fromCommission();
}

/* ══════════════════════════════════════
   CUSTOMER DUE
══════════════════════════════════════ */
function onPhoneInput() {
  clearTimeout(phoneCheckTimer);
  dueLookupId++;
  currentCustomerDue = 0;
  document.getElementById('prev-due-row').style.display = 'none';
  calcTotals();
  const indicator = document.getElementById('phone-check-indicator');
  if (indicator) indicator.textContent = '';
  phoneCheckTimer = setTimeout(fetchDue, 450);
}

async function refreshCustomersIfStale() {
  // অন্য কোনো স্টাফ শিফট চলাকালীন নতুন গ্রাহক যোগ করলে যেন সেটা মিস না হয়,
  // তাই ফোকাসে গেলে (কিন্তু বারবার না) ব্যাকগ্রাউন্ডে তালিকা রিফ্রেশ করা হচ্ছে।
  if (customersLoading || Date.now() - lastCustomerFetch < 2 * 60 * 1000) return;
  customersLoading = true;
  try {
    let page = 1, customers = [], more = true;
    while (more) {
      const cRes = await api(`/sales/customers/?page=${page}&page_size=1000`);
      customers = customers.concat(cRes.results);
      more = !!cRes.next; page++;
    }
    allCustomers = customers;
    lastCustomerFetch = Date.now();
  } catch (e) { /* Keep the existing customer list on background failure. */ }
  finally { customersLoading = false; }
}

async function fetchDue() {
  const lookup = ++dueLookupId;
  const phone      = document.getElementById('cus-phone')?.value.trim() || '';
  const row        = document.getElementById('prev-due-row');
  const val        = document.getElementById('t-prev-due');
  const indicator  = document.getElementById('phone-check-indicator');
  currentCustomerDue = 0;
  if (indicator) indicator.innerHTML = '';

  if (phone && phone.length >= 11) {
    try {
      const res = await api(`/sales/customers/find_by_phone/?phone=${encodeURIComponent(phone)}`);
      if (lookup !== dueLookupId || document.getElementById('cus-phone').value.trim() !== phone) return;
      if (res && res.id) {
        document.getElementById('cus-name').value = res.name;
        currentCustomerDue = parseFloat(res.total_due || 0);
        if (indicator) indicator.innerHTML = `${icon('check', 12)} পরিচিত গ্রাহক`;
      }
    } catch (_) {}
  }

  if (lookup !== dueLookupId) return;
  if (currentCustomerDue > 0) {
    row.style.display = 'flex';
    val.textContent   = `৳${money(currentCustomerDue)}`;
  } else {
    row.style.display = 'none';
  }
  calcTotals();
}

/* ══════════════════════════════════════
   TOTALS
══════════════════════════════════════ */
function defaultCommissionPct() {
  return Math.min(100, Math.max(0, parseFloat(document.getElementById('commission')?.value || 0)));
}

function itemGross(item) {
  return item.price * item.qty;
}

function itemBaseCommission(item) {
  const globalPct = defaultCommissionPct();
  const ownPct    = Math.min(100, Math.max(0, parseFloat(item.defaultCommission || 0)));
  const saleType  = document.getElementById('sale-type')?.value || 'retail';

  if (saleType === 'wholesale') {
    // পাইকারি গ্রাহকের জন্য কমিশন% একটা নির্দিষ্ট চুক্তির হার — বইয়ের নিজস্ব
    // ডিফল্ট কমিশন উপেক্ষা করে পুরোপুরি রিপ্লেস হয়।
    return globalPct > 0 ? globalPct : ownPct;
  }

  // খুচরা বিক্রয়ে বইয়ের নিজস্ব ডিফল্ট কমিশন (MRP থেকে দোকানের স্বাভাবিক ছাড়) সবসময় বহাল থাকে;
  // "ডিসকাউন্ট ৳ / কমিশন %" বক্সে কিছু লিখলে সেটা তার উপর বাড়তি ছাড় হিসেবে যোগ হয়,
  // প্রতিস্থাপন করে না — নাহলে ছোট একটা বাড়তি ছাড় দিতে গিয়ে দাম উল্টো বেড়ে যেত।
  return Math.min(100, ownPct + globalPct);
}

function itemCommissionPct(item) {
  return Math.min(100, itemBaseCommission(item) + Math.max(0, parseFloat(item.extraCommission || 0)));
}

function itemDiscount(item) {
  const gross = itemGross(item);
  return Math.round(Math.min(gross, gross * itemCommissionPct(item) / 100)*100)/100;
}

function itemNet(item) {
  return Math.max(0, itemGross(item) - itemDiscount(item));
}

function fromCommission() {
  const sub  = cart.reduce((s,i) => s + itemGross(i), 0);
  const comm = defaultCommissionPct();
  if (document.getElementById('discount'))
    document.getElementById('discount').value = (sub * comm / 100).toFixed(2);
  renderCart();
  calcTotals();
}

function calcTotals(manualDisc = false) {
  const sub  = cart.reduce((s,i) => s + itemGross(i), 0);
  const manualDiscount = parseFloat(document.getElementById('discount')?.value || 0);

  if (manualDisc && sub > 0) {
    document.getElementById('commission').value = Math.min(100, (manualDiscount/sub)*100).toFixed(6);
    renderCart();
  }
  if (!manualDisc) document.getElementById('discount').value = (sub * defaultCommissionPct() / 100).toFixed(2);

  const disc = cart.reduce((s,i) => s + itemDiscount(i), 0);

  const extra = (parseFloat(document.getElementById('pack')?.value||0))
              + (parseFloat(document.getElementById('courier')?.value||0))
              + (parseFloat(document.getElementById('txfee')?.value||0));

  const total  = Math.max(0, sub - disc + extra);
  const paid   = Math.max(0, parseFloat(document.getElementById('paid')?.value || 0));
  const due    = Math.max(0, total - paid);
  const change = Math.max(0, paid - total);
  const grand  = currentCustomerDue + due;
  const totalQty = cart.reduce((s,i) => s + i.qty, 0);

  setText('t-items',    `${cart.length}`);
  setText('t-qty',      `${totalQty}`);
  setText('t-sub',      `৳${money(sub)}`);
  setText('t-disc',     `−৳${money(disc)}`);
  setText('t-extra',    `+৳${money(extra)}`);
  setText('t-total',    `৳${money(total)}`);
  setText('t-due',      `৳${money(due)}`);
  setText('t-change',   `৳${money(change)}`);
  setText('t-grand-due',`৳${money(grand)}`);

  const changeRow = document.getElementById('change-row');
  if (changeRow) changeRow.style.display = change > 0 ? 'flex' : 'none';

  const payMethod = document.getElementById('pay-method')?.value;
  const zeroWarn = document.getElementById('paid-zero-warn');
  if (zeroWarn) zeroWarn.style.display = (cart.length > 0 && total > 0 && paid === 0 && payMethod !== 'credit') ? 'block' : 'none';

  const overpayWarn = document.getElementById('paid-overpay-warn');
  const looksLikeTypo = total > 0 && paid > total * 20 && (paid - total) > 500;
  if (overpayWarn) overpayWarn.style.display = looksLikeTypo ? 'block' : 'none';

  const btn = document.getElementById('complete-btn');
  if (btn) btn.disabled = cart.length === 0 || saleSubmitting || catalogLoading;
  const checkout = document.getElementById('checkout-btn');
  if (checkout) checkout.disabled = cart.length === 0 || saleSubmitting || catalogLoading;
  setText('cart-total', '৳' + money(total));
  setText('mobile-cart', 'কার্ট · ' + money(totalQty) + ' কপি · ৳' + money(total) + ' ↑');
  document.getElementById('mobile-cart').hidden = !cart.length;
  setText('discount-explanation', document.getElementById('sale-type').value === 'wholesale' ? 'পাইকারি কমিশন বইয়ের স্বাভাবিক ছাড়ের পরিবর্তে ব্যবহৃত হয়। কমিশন ০ হলে বইয়ের স্বাভাবিক ছাড় থাকবে।' : 'খুচরা বিক্রয়ে এই ছাড় বইয়ের স্বাভাবিক ছাড়ের সঙ্গে যোগ হবে।');
  setText('adjustments-indicator', extra > 0 || defaultCommissionPct() > 0 ? ' · চালু' : '');
  document.getElementById('grand-due-row').hidden = currentCustomerDue <= 0;
  saveCartDraft();
}

function setText(id, v) {
  const el = document.getElementById(id);
  if (el) el.textContent = v;
}

/* ══════════════════════════════════════
   PAID AMOUNT HELPERS
══════════════════════════════════════ */
function onPaidInput() {
  const paidInput = document.getElementById('paid');
  if (paidInput && parseFloat(paidInput.value) < 0) paidInput.value = 0;
  calcTotals();
}

function currentBillTotal() {
  const sub  = cart.reduce((s,i) => s + itemGross(i), 0);
  const disc = cart.reduce((s,i) => s + itemDiscount(i), 0);
  const extra = (parseFloat(document.getElementById('pack')?.value||0))
              + (parseFloat(document.getElementById('courier')?.value||0))
              + (parseFloat(document.getElementById('txfee')?.value||0));
  return Math.round(Math.max(0, sub - disc + extra) * 100) / 100;
}

function setPaidFull() {
  document.getElementById('paid').value = currentBillTotal();
  calcTotals();
}

function setPaidAmount(v) {
  document.getElementById('paid').value = v;
  calcTotals();
}

function onPayMethodChange() {
  const method = document.getElementById('pay-method')?.value;
  const row = document.getElementById('paid-quick-row');
  if (method === 'credit') {
    document.getElementById('paid').value = 0;
    document.getElementById('paid').disabled = true;
    if (row) row.style.display = 'none';
  } else if (row) {
    document.getElementById('paid').disabled = false;
    row.style.display = 'flex';
  }
  calcTotals();
}

/* ══════════════════════════════════════
   COMPLETE SALE
══════════════════════════════════════ */
function checkoutError(text,field) {
  const el = document.getElementById('checkout-error');
  el.textContent = text; el.hidden = false;
  if (field) field.focus(); else el.focus();
}
async function completeSale() {
  if (saleSubmitting || catalogLoading || !cart.length) return;
  const error = document.getElementById('checkout-error'); error.hidden = true;
  const numeric = ['commission','discount','pack','courier','txfee','paid'];
  for (const id of numeric) {
    const el = document.getElementById(id);
    if (!Number.isFinite(Number(el.value)) || Number(el.value)<0 || !el.checkValidity()) {
      if (['commission','discount','pack','courier','txfee'].includes(id)) {
        showCartStep(); document.querySelector('.order-options').open = true;
      }
      checkoutError('টাকার অঙ্ক ও ছাড়ের মান সঠিকভাবে লিখুন।',el); return;
    }
  }
  const customerName = document.getElementById('cus-name').value.trim();
  const customerPhone = document.getElementById('cus-phone').value.trim();
  const paid = Number(document.getElementById('paid').value)||0;
  if (paid < currentBillTotal() && (!customerName || !customerPhone)) {
    checkoutError('বাকি রাখার জন্য ক্রেতার নাম ও ফোন নম্বর লিখুন।',document.getElementById(!customerName?'cus-name':'cus-phone')); return;
  }
  for (const item of cart) {
    if (!Number.isInteger(item.qty) || item.qty<1 || item.qty>item.max) { checkoutError('কার্টে বইয়ের পরিমাণ ও স্টক যাচাই করুন।'); return; }
  }
  saleSubmitting = true;
  const btn = document.getElementById('complete-btn');
  btn.disabled = true; btn.textContent = 'বিল সেভ হচ্ছে…';
  const payload = {
    customer_name: customerName || 'Walk-in Customer', customer_phone: customerPhone,
    discount:0, packaging_charge:Number(document.getElementById('pack').value)||0,
    courier_charge:Number(document.getElementById('courier').value)||0,
    transaction_fee:Number(document.getElementById('txfee').value)||0,
    // Record the bill payment only; excess cash is change returned to the customer.
    paid_amount:Math.min(paid,currentBillTotal()),
    payment_method:document.getElementById('pay-method').value,
    sale_type:document.getElementById('sale-type').value,
    items:cart.map(i=>({book:i.bid,quantity:i.qty,unit_price:i.price,discount:Math.round(itemDiscount(i)*100)/100}))
  };
  const controls = [...document.querySelectorAll('.sales-desk input,.sales-desk select,.sales-desk button')];
  controls.forEach(el=>{el.disabled=true;});
  let sale;
  try { sale = await api('/sales/','POST',payload); }
  catch(e) {
    saleSubmitting = false; controls.forEach(el=>{el.disabled=false;});
    document.getElementById('paid').disabled = document.getElementById('pay-method').value === 'credit';
    btn.textContent='বিল তৈরি করুন'; calcTotals();
    checkoutError(formatApiError(e)); return;
  }
  // A catalog refresh failure after a successful POST must never invite resubmission.
  cart = []; currentCustomerDue=0; clearCartDraft();
  try { allBooks = await loadAllBooks(); }
  catch (_) { allBooks.forEach(b=>{const item=payload.items.find(i=>i.book===b.id);if(item)b.shop_quantity=Math.max(0,Number(b.shop_quantity)-item.quantity);}); }
  saleSubmitting = false;
  renderPOS(document.getElementById('pos-root'));
  showSuccessModal({...sale, cash_received:paid, change_due:Math.max(0,paid-Number(sale.total))});
}

function showSuccessModal(sale) {
  const html = `
    <div class="sale-modal-bg" id="sale-modal">
      <div class="sale-modal" role="dialog" aria-modal="true" aria-labelledby="sale-success-title" tabindex="-1">
        <div class="modal-check">${icon('check', 34)}</div>
        <div class="modal-title" id="sale-success-title">বিক্রয় সেভ হয়েছে</div>
        <div class="modal-sub">
          ইনভয়েস নম্বর:
          <div class="modal-invoice">#${escapeHTML(sale.invoice_number)}</div>
          মোট বিল: ৳${money(sale.total)}
          ${sale.change_due > 0 ? `<div class="modal-change">ক্রেতাকে ফেরত দিন <strong>৳${money(sale.change_due)}</strong></div>` : ''}
          ${Number(sale.due_amount)>0 ? `<div class="modal-due">এই বিলে বাকি: ৳${money(sale.due_amount)}</div>` : ''}
        </div>
        <div class="modal-actions">
          <a href="/api/sales/${sale.id}/print_invoice/"
             class="btn btn-primary"
             target="_blank"
             onclick="closeModal()"
             style="text-decoration:none; border-radius:12px; min-height:48px; font-size:17px;">
            ${icon('printer', 16)} ইনভয়েস প্রিন্ট করুন
          </a>
          <button class="btn btn-ghost" onclick="closeModal()" style="border-radius:12px; min-height:44px;">
            নতুন বিক্রয় শুরু করুন →
          </button>
          <button class="btn btn-ghost" onclick="undoLastSale(${sale.id})" style="border-radius:12px; min-height:40px; color:var(--danger); font-size:16px;">
            ${icon('x-circle', 14)} ভুল হয়েছে? এই বিক্রয় বাতিল করুন
          </button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
  const modal = document.querySelector('.sale-modal');
  modal.focus();
  modal.addEventListener('keydown', event => {
    if (event.key === 'Escape') { closeModal(); return; }
    if (event.key !== 'Tab') return;
    const controls = [...modal.querySelectorAll('a,button')];
    const first = controls[0], last = controls[controls.length-1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === modal)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && (document.activeElement === last || document.activeElement === modal)) { event.preventDefault(); first.focus(); }
  });
}

function closeModal() {
  document.getElementById('sale-modal')?.remove();
  document.getElementById('book-search')?.focus();
}

async function undoLastSale(saleId) {
  if (returnSubmitting) return;
  returnSubmitting = true;
  // বিক্রয় রেকর্ড সরাসরি ডিলিট করা হয় না (হিসাব/অডিট ট্রেইল ঠিক রাখতে) — বরং সব বই
  // return_items দিয়ে ফেরত নেওয়া হয়, যা স্টক ও বাকির হিসাব ঠিকভাবে রিভার্স করে এবং
  // ইনভয়েসে একটা রিটার্ন রেকর্ড হিসেবে থেকে যায় (invoices.html-এর মতোই)।
  const ok = await confirmModal('এই পুরো বিক্রয়টা বাতিল করতে চান? সব বই স্টকে ফিরে যাবে এবং বাকির হিসাব সমন্বয় হবে।', { danger: true });
  if (!ok) { returnSubmitting = false; return; }
  try {
    const sale = await api(`/sales/${saleId}/`);
    const items = (sale.items || []).map(i => ({ book_id: i.book, quantity: i.quantity }));
    if (!items.length) { toast('ফেরত দেওয়ার মতো কোনো বই পাওয়া যায়নি।', 'error'); return; }
    await api(`/sales/${saleId}/return_items/`, 'POST', { items, reason: 'POS-এ তাৎক্ষণিক ভুল সংশোধন' });
    closeModal();
    try { allBooks = await loadAllBooks(); filterBooks(); } catch (_) { setText('result-count','স্টক আপডেট দেখতে পেজ রিফ্রেশ করুন'); }
    toast('বিক্রয়টি বাতিল করা হয়েছে, বই স্টকে ফিরে গেছে।');
    closeModal();
  } catch (e) {
    const msg = e && typeof e === 'object' ? (e.error || e.detail || JSON.stringify(e)) : String(e);
    toast('বাতিল করা যায়নি: ' + escapeHTML(msg), 'error');
  } finally { returnSubmitting = false; }
}

document.addEventListener('DOMContentLoaded', initPOS);
