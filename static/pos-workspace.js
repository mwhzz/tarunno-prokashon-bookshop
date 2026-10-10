
let allBooks             = [];
let allCustomers         = [];
let lastCustomerFetch    = 0;
let cart                 = [];
let currentCustomerDue   = 0;

let savedRetailCommission = 0;
let phoneCheckTimer      = null;
let saleSubmitting = false;
let catalogLoading = true;
let dueLookupId = 0;
let feedbackTimer;
let draftReady = false;
let customersLoading = false;
let returnSubmitting = false;
let posStep = 'books';
let settlementMode = null;
let customerDetailsOpen = false;
let reviewedTotalCents = null;
let pendingSalePayload = null;
let pendingReceived = 0;
let checkoutRequestId = null;

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
  try { localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({cart,fields,savedRetailCommission,settlementMode,customerDetailsOpen,reviewedTotalCents,pendingSalePayload,pendingReceived,checkoutRequestId})); }
  catch (_) { setText('draft-status','খসড়া রাখা যায়নি। পেজ বন্ধ করার আগে বিল শেষ করুন।'); }
}
function clearCartDraft() { try {localStorage.removeItem(CART_STORAGE_KEY);} catch (_) {} }
function loadCartDraft() {
  try {
    const raw = localStorage.getItem(CART_STORAGE_KEY); if (!raw) return;
    const saved = JSON.parse(raw), items = Array.isArray(saved) ? saved : saved.cart;
    if (!Array.isArray(items) || !items.length) return;
    pendingSalePayload=saved.pendingSalePayload||null;pendingReceived=Number(saved.pendingReceived)||0;checkoutRequestId=saved.checkoutRequestId||null;
    cart = items.flatMap(item => {
      if(pendingSalePayload&&Number.isInteger(item.qty)&&item.qty>0&&Number.isFinite(Number(item.price)))return [{...item}];
      const book = allBooks.find(b => b.id === item.bid);
      if (!book || !book.is_active) return [];
      const qty = Math.min(Number(book.shop_quantity)||0,Math.max(0,Math.floor(Number(item.qty)||0)));
      if (!qty) return [];
      return [{bid:book.id,title:book.title,price:Number(book.mrp)||Number(book.selling_price)||0,qty,max:Number(book.shop_quantity)||0,defaultCommission:Number(book.commission)||0,extraCommission:Math.min(100,Math.max(0,Number(item.extraCommission)||0))}];
    });
    if (cart.length && saved.fields) {
      DRAFT_FIELDS.forEach(id => { if (saved.fields[id] !== undefined) document.getElementById(id).value = saved.fields[id]; });
      savedRetailCommission = Number(saved.savedRetailCommission)||0;
      settlementMode=['full','partial','due'].includes(saved.settlementMode)?saved.settlementMode:null;
      customerDetailsOpen=!!saved.customerDetailsOpen;reviewedTotalCents=Number.isInteger(saved.reviewedTotalCents)?saved.reviewedTotalCents:null;
      document.getElementById('ws-section').style.display = saved.fields['sale-type'] === 'wholesale' ? 'block' : 'none';
      onPayMethodChange();
    }
    if (cart.length) setText('draft-status','আগের অসম্পূর্ণ বিল ফিরে এসেছে। বর্তমান দাম ও স্টক যাচাই করুন।');
  } catch (_) { clearCartDraft(); }
}

/* ══════════════════════════════════════
   ERROR FORMATTING
══════════════════════════════════════ */
function formatApiError(error) {
  const flatten=value=>Array.isArray(value)?value.flatMap(flatten):value&&typeof value==='object'?Object.values(value).flatMap(flatten):[String(value||'')];
  if(error?.message)return error.message;
  const parts=flatten(error).filter(Boolean);
  return parts.length?parts.join(' · '):'সেভ করা যায়নি। আবার চেষ্টা করুন।';
}

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
    if (document.getElementById('cus-phone').value&&!pendingSalePayload) fetchDue();
    if(pendingSalePayload){goToPayment();lockPendingCheckout();checkoutError('আগের বিক্রয় সেভ হয়েছে কি না নিশ্চিত করা হয়নি। নিচের যাচাই বোতাম চাপুন। নতুন করে বিল বানাবেন না।');}
  } catch (e) {
    root.innerHTML = `<div class="pos-loading" role="alert"><h2>বইয়ের তালিকা লোড হয়নি</h2><p>সংযোগ চেক করে আবার চেষ্টা করুন। আগের খসড়া রাখা আছে।</p><button class="pos-button secondary" onclick="initPOS()">আবার চেষ্টা করুন</button></div>`;
  }
}

function renderPOS(container) {
  posStep = 'books';
  container.innerHTML = `
  <div class="counter-steps" aria-label="বিক্রয়ের নিয়ম"><span id="cart-step" class="active">১ <b>বই ও পরিমাণ লিখুন</b></span><i>→</i><span id="payment-step">২ <b>ছাড় ও টাকা মিলিয়ে জমা দিন</b></span></div>
  <div id="checkout-error" role="alert" tabindex="-1" hidden></div>
  <section id="cart-stage" class="ledger-card">
    <header class="ledger-heading"><div><h2>বিক্রির খাতা</h2><p>বই বাছুন। পরিমাণ লিখুন। দাম নিজে বসবে।</p></div><button type="button" class="text-button danger" onclick="clearCart()">খাতা খালি করুন</button></header>
    <div class="ledger-search-wrap"><label for="book-search">কোন বই বিক্রি করবেন?</label><div class="ledger-search"><span>${icon('search',21)}</span><input id="book-search" placeholder="বইয়ের নাম, কোড বা ISBN লিখুন…" oninput="openBookPicker()" onfocus="openBookPicker()" onkeydown="handleSearchKey(event)" autocomplete="off" role="combobox" aria-expanded="false" aria-controls="book-picker" aria-haspopup="dialog"><button type="button" class="picker-toggle" onclick="openBookPicker();document.getElementById('book-search').focus()">বইয়ের তালিকা ↓</button></div>
      <div id="book-picker" class="book-picker" role="dialog" aria-label="বই বাছাই করুন" hidden><div class="picker-caption"><span id="result-count" role="status"></span><button type="button" class="text-button" onclick="closeBookPicker()">বন্ধ করুন ×</button></div><details class="picker-options"><summary>বিভাগ বা স্টক দিয়ে খুঁজুন</summary><div class="pos-filter-row"><label>বিভাগ<select id="filter-group" onchange="filterBooks()"><option value="">সব বিভাগ</option></select></label><label>সাজান<select id="filter-sort" onchange="filterBooks()"><option value="name">নাম অনুযায়ী</option><option value="stock-asc">কম স্টক আগে</option><option value="price-asc">কম দাম আগে</option><option value="price-desc">বেশি দাম আগে</option></select></label><label class="filter-check"><input id="filter-instock" type="checkbox" checked onchange="filterBooks()">দোকানে আছে</label><button type="button" class="text-button" onclick="resetFilters()">সব বই</button></div></details><div id="book-grid" onkeydown="handleGridKey(event)"></div></div>
    </div>
    <div class="ledger-table-wrap"><table class="cart-table"><thead><tr><th>বইয়ের নাম</th><th>পরিমাণ</th><th>প্রতি বইয়ের দাম</th><th>মোট টাকা</th><th><span class="sr-only">মুছুন</span></th></tr></thead><tbody id="cart-scroll"></tbody></table></div>
    <div class="ledger-bottom"><div><span id="cart-count-lbl">এখনো কোনো বই লেখা হয়নি</span><p id="draft-status" role="status">এই ব্রাউজারে অসম্পূর্ণ খাতা রাখা হয়।</p></div><div class="ledger-payable"><div><span>এই বিলের মোট</span><strong id="cart-total">৳০</strong><small><b id="t-items">০</b> ধরনের বই · <b id="t-qty">০</b> কপি</small></div><button type="button" id="checkout-btn" class="pos-button primary" onclick="goToPayment()" disabled>টাকা জমার অংশে যান ↓</button></div></div>
  </section>
  <div class="sale-entry-layout"><section class="sale-options-card"><div class="ledger-notes"><section class="order-options"><h2>ছাড় ও বাড়তি খরচ</h2><p class="field-help">স্বাভাবিক দাম নিজে বসবে। বাড়তি ছাড় বা খরচ থাকলে নিচে লিখুন।</p><div class="options-body"><div class="bf-field sale-mode"><label for="sale-type">বিক্রয়ের ধরন</label><select id="sale-type" onchange="toggleSaleType()"><option value="retail">খুচরা বিক্রয়</option><option value="wholesale">পাইকারি বিক্রয়</option></select></div><div id="ws-section" style="display:none"><div class="bf-field"><label for="wholesale-search">আগের পাইকারি ক্রেতা খুঁজুন</label><input id="wholesale-search" placeholder="নাম, কোড বা ফোন" oninput="searchWS()" autocomplete="off"><div id="wholesale-results"></div><input id="selected-ws-id" type="hidden"></div><p class="field-help">পুরোনো ক্রেতা বাছলে তাঁর কমিশন নিজে বসবে। নতুন হলে টাকা নেওয়ার ধাপে নাম ও ফোন লিখুন।</p></div><p id="discount-explanation" class="field-help"></p><div class="bf-row"><div class="bf-field"><label for="commission">ছাড় / কমিশন (%)</label><input id="commission" type="number" value="0" min="0" max="100" step="any" oninput="fromCommission()"></div><div class="bf-field"><label for="discount">সমপরিমাণ ছাড় (৳)</label><input id="discount" type="number" value="0" min="0" step="0.01" oninput="calcTotals(true)"></div></div><div class="bf-row charges"><div class="bf-field"><label for="pack">প্যাকিং (৳)</label><input id="pack" type="number" value="0" min="0" step="0.01" oninput="calcTotals()"></div><div class="bf-field"><label for="courier">কুরিয়ার (৳)</label><input id="courier" type="number" value="0" min="0" step="0.01" oninput="calcTotals()"></div><div class="bf-field"><label for="txfee">অন্য ফি (৳)</label><input id="txfee" type="number" value="0" min="0" step="0.01" oninput="calcTotals()"></div></div><span id="adjustments-indicator" class="field-help"></span></div></section></div>
</section>
  <section id="payment-stage">
    <div class="payment-section-heading"><h2>টাকা জমা করুন</h2><span>বই বা পরিমাণ উপরের খাতাতেই বদলাতে পারবেন</span></div>
    <div class="payment-layout"><div class="payment-card">
      <div class="payment-title"><span>ক্রেতার কাছ থেকে নিন</span><strong id="t-total">৳০</strong><p id="checkout-count"></p></div>
      <fieldset class="settlement-choices"><legend>টাকা পেয়েছেন?</legend><div><button type="button" id="mode-full" aria-pressed="false" onclick="chooseSettlement('full')"><span class="choice-mark">✓</span><b>পুরো টাকা পেয়েছি</b></button><button type="button" id="mode-partial" aria-pressed="false" onclick="chooseSettlement('partial')"><span class="choice-mark">◐</span><b>কিছু টাকা পেয়েছি</b></button><button type="button" id="mode-due" aria-pressed="false" onclick="chooseSettlement('due')"><span class="choice-mark">○</span><b>বাকি থাকবে</b></button></div></fieldset>
      <div id="payment-entry" hidden><fieldset id="payment-methods" class="method-choices"><legend>কীভাবে পেয়েছেন?</legend><div><button type="button" id="method-cash" aria-pressed="true" onclick="choosePaymentMethod('cash')">নগদ</button><button type="button" id="method-mobile" aria-pressed="false" onclick="choosePaymentMethod('mobile')">বিকাশ / নগদ</button><button type="button" id="method-bank" aria-pressed="false" onclick="choosePaymentMethod('bank')">ব্যাংক</button></div></fieldset><select id="pay-method" hidden><option value="cash">নগদ</option><option value="mobile">বিকাশ / নগদ</option><option value="bank">ব্যাংক</option><option value="credit">বাকি</option></select>
      <div class="received-field" id="received-field"><label for="paid" id="paid-label">হাতে কত টাকা এসেছে?</label><div class="received-input"><span>৳</span><input id="paid" type="text" inputmode="decimal" autocomplete="off" value="0" oninput="onPaidInput()" aria-describedby="payment-input-hint"></div><p id="payment-input-hint" class="field-help"></p><div id="cash-shortcuts" class="cash-shortcuts"></div></div>
      <div class="payment-outcome" id="payment-outcome" role="status" aria-live="polite"><span id="outcome-label"></span><strong id="outcome-amount"></strong><p id="outcome-help"></p></div>
      <div class="customer-area" id="customer-area" hidden><h3 id="customer-heading">কার নামে বাকি রাখবেন?</h3><div class="bf-row"><div class="bf-field"><label for="cus-name">ক্রেতার নাম</label><input id="cus-name" maxlength="200" placeholder="নাম লিখুন" oninput="saveCartDraft()"></div><div class="bf-field"><label for="cus-phone">ফোন নম্বর</label><input id="cus-phone" type="tel" maxlength="20" placeholder="01XXXXXXXXX" oninput="onPhoneInput();saveCartDraft()" onfocus="refreshCustomersIfStale()"><small id="phone-check-indicator"></small></div></div><div id="prev-due-row" class="previous-due" style="display:none">আগের বাকি <strong id="t-prev-due"></strong><p>আগের বাকি এই বিলে আদায় হচ্ছে না। <a href="/sales/due/" target="_blank" rel="noopener">বাকি আদায় করুন ↗</a></p></div><p id="customer-help" class="field-help"></p></div>
      <button type="button" id="customer-toggle" class="text-button" onclick="toggleCustomerDetails()">বিলে ক্রেতার নাম / ফোন লিখতে চান?</button>
      </div><footer class="payment-finish"><button type="button" id="complete-btn" class="pos-button primary wide" onclick="completeSale()" disabled>আগে টাকা পেয়েছেন কি না বাছুন</button><p id="finish-help">বিল শেষ করলেই বিক্রয় ও স্টক সেভ হবে।</p></footer>
    </div><aside class="bill-preview"><h2>খাতার হিসাব</h2><div id="review-lines"></div><div class="review-totals"><div><span>ছাড়ের আগে</span><b id="t-sub"></b></div><div><span>মোট ছাড়</span><b id="t-disc"></b></div><div><span>বাড়তি খরচ</span><b id="t-extra"></b></div><div class="review-net"><span>এই বিল</span><strong id="review-total"></strong></div><div><span>বিলে জমা হবে</span><b id="recorded-paid"></b></div><div><span>এই বিলে বাকি</span><b id="t-due"></b></div><div id="grand-due-row" hidden><span>আগেরসহ মোট বাকি</span><b id="t-grand-due"></b></div></div><div class="price-assurance">বইয়ের নির্ধারিত ছাড় নিজে থেকেই হিসাব হয়েছে।</div></aside></div>
  </section></div><div id="cart-feedback" class="cart-feedback" role="status" aria-live="polite"></div>`;
  updateGroupOptions(); filterBooks(); renderCart(); calcTotals();
}
function escapeHTML(value) {return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function openBookPicker() {
  if (saleSubmitting || pendingSalePayload) return;
  filterBooks();document.getElementById('book-picker').hidden=false;
  document.getElementById('book-search').setAttribute('aria-expanded','true');
}
function closeBookPicker() {
  document.getElementById('book-picker').hidden=true;
  document.getElementById('book-search').setAttribute('aria-expanded','false');
}
function showCartStep() {
  if (saleSubmitting || pendingSalePayload) return;
  posStep='books';
  document.getElementById('cart-stage').scrollIntoView({behavior:'smooth',block:'start'});
  document.getElementById('book-search').focus();
}
function goToPayment() {
  if (!cart.length || saleSubmitting) return;
  if (reviewedTotalCents!==billCents() && !pendingSalePayload) {settlementMode=null;document.getElementById('paid').value='0';}
  reviewedTotalCents=billCents();posStep='payment';closeBookPicker();
  calcTotals();document.getElementById('payment-stage').scrollIntoView({behavior:'smooth',block:'start'});
  document.getElementById(settlementMode?'complete-btn':'mode-full').focus();
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
    (!inStock || Number(b.shop_quantity||0) > 0)
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
  if (e.key === 'Escape') {closeBookPicker();return;}
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    document.querySelector('#book-grid .book-row:not(:disabled)')?.focus();
    return;
  }
  if (e.key !== 'Enter') return;
  e.preventDefault();
  const matches = getFilteredBooks();
  if (matches.length === 1) {
    addToCart(matches[0].id);
    document.getElementById('book-search').value='';filterBooks();closeBookPicker();
  } else if (matches.length > 1) {
    // একাধিক বই মিললে তীর-চিহ্ন (↓) দিয়ে লিস্ট থেকে বেছে নেওয়া যাবে, মাউস ছাড়াই
    document.querySelector('#book-grid .book-row:not(:disabled)')?.focus();
  } else {
    toast('কোনো বই পাওয়া যায়নি', 'error');
  }
}

function handleGridKey(e) {
  const row = e.target.closest('.book-row');
  if (!row) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const rows = Array.from(document.querySelectorAll('#book-grid .book-row:not(:disabled)'));
    const idx = rows.indexOf(row);
    const next = e.key === 'ArrowDown' ? rows[idx + 1] : rows[idx - 1];
    if (next) next.focus();
    else if (e.key === 'ArrowUp') document.getElementById('book-search')?.focus();
  } else if (e.key === 'Escape') {
    closeBookPicker();document.getElementById('book-search')?.focus();
  }
}

function defaultBookPrice(book) {
  const mrp=Number(book.mrp)||0, pct=Math.min(100,Math.max(0,Number(book.commission)||0));
  return mrp>0 ? mrp*(1-pct/100) : Number(book.selling_price)||0;
}
function filterBooks() {
  const list=getFilteredBooks(),grid=document.getElementById('book-grid');if(!grid)return;
  setText('result-count',money(list.length)+'টি বই · বাছলেই খাতায় বসবে');
  if(!list.length){grid.innerHTML='<div class="picker-empty">বই পাওয়া যায়নি। অন্য নাম বা কোড লিখুন, অথবা স্টক ফিল্টার সরান।</div>';return;}
  grid.innerHTML=list.map(b=>{
    const out=Number(b.shop_quantity)<=0,selected=cart.find(i=>i.bid===b.id);
    return `<button type="button" class="book-row ${out?'out':''}" ${out?'disabled':''} onclick="addToCart(${b.id})" aria-label="${escapeHTML(b.title)} খাতায় লিখুন"><span><strong>${escapeHTML(b.title)}</strong><small>${escapeHTML(b.author||'')}${b.product_code?' · কোড '+escapeHTML(b.product_code):''} · ${out?'দোকানে নেই':money(b.shop_quantity)+' কপি আছে'}${selected?' · খাতায় '+money(selected.qty)+' কপি':''}</small></span><b>৳${money(defaultBookPrice(b))}</b><span class="book-write">${out?'স্টক নেই':'+ লিখুন'}</span></button>`;
  }).join('');
}

function addToCart(id) {
  const book = allBooks.find(b => b.id === id);
  if (saleSubmitting || pendingSalePayload || !book || book.shop_quantity <= 0 || !book.is_active) return;

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
  invalidatePaymentReview();renderCart();
  calcTotals();
  closeBookPicker();document.getElementById('book-search').value='';
  const quantity=document.getElementById('qty-'+id);quantity?.focus();quantity?.select();

}

function changeQty(id, d) {
  if (saleSubmitting || pendingSalePayload) return;
  const item = cart.find(i => i.bid === id);
  if (!item) return;
  if(d<0&&item.qty<=1)return;
  if (d > 0 && item.qty >= item.max) { toast(`দোকানে মাত্র ${item.max}টি আছে`, 'error'); return; }
  item.qty = Math.max(0, Math.min(item.max, item.qty + d));
  if (item.qty === 0) cart = cart.filter(i => i.bid !== id);
  invalidatePaymentReview();renderCart();
  calcTotals();
}

function setQty(id,value) {
  if(saleSubmitting||pendingSalePayload)return;
  const item=cart.find(i=>i.bid===id);if(!item)return;
  let qty=Number(normalizeDigits(value));
  if(item.max<1){document.getElementById('qty-'+id).value=item.qty;toast('দোকানে বইটি নেই। খাতা থেকে মুছে দিন।','error');return;}
  if(!Number.isInteger(qty)||qty<1){document.getElementById('qty-'+id).value=item.qty;toast('পরিমাণে ১ বা তার বেশি পুরো সংখ্যা লিখুন।','error');return;}
  if(qty>item.max){qty=item.max;toast('দোকানে '+money(item.max)+' কপি আছে।','error');}
  item.qty=qty;invalidatePaymentReview();
  const row=document.getElementById('qty-'+id).closest?.('tr');row?.classList.remove('stock-invalid');row?.querySelector('.stock-error')?.remove();
  document.getElementById('qty-'+id).value=qty;
  document.getElementById('minus-'+id).disabled=qty<=1;document.getElementById('plus-'+id).disabled=qty>=item.max;
  document.getElementById('unit-'+id).innerHTML=unitPriceHTML(item);setText('line-total-'+id,'৳'+money(itemNet(item)));
  calcTotals();filterBooks();
}

function removeItem(id) {
  if (saleSubmitting || pendingSalePayload) return;
  cart = cart.filter(i => i.bid !== id);
  if (!cart.length) resetGlobalDiscount();
  invalidatePaymentReview();renderCart();
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
  if (saleSubmitting || pendingSalePayload) return;
  const item = cart.find(i => i.bid === id);
  if (!item) return;
  item.extraCommission = Math.min(100, Math.max(0, parseFloat(value || 0)));
  invalidatePaymentReview();calcTotals();
}

async function clearCart() {
  if (!cart.length || saleSubmitting || pendingSalePayload) return;
  const ok=await confirmModal('খাতার সব বই, ছাড়, বাড়তি খরচ ও ক্রেতার তথ্য মুছবেন?',{danger:true});
  if (!ok) return;
  cart = [];
  clearCartDraft();settlementMode=null;reviewedTotalCents=null;currentCustomerDue=0;customerDetailsOpen=false;savedRetailCommission=0;
  ['pack','courier','txfee','paid'].forEach(id=>document.getElementById(id).value='0');
  ['cus-name','cus-phone','wholesale-search','selected-ws-id'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('pay-method').value='cash';document.getElementById('sale-type').value='retail';document.getElementById('ws-section').style.display='none';document.getElementById('prev-due-row').style.display='none';
  clearTimeout(phoneCheckTimer);dueLookupId++;
  resetGlobalDiscount();
  renderCart();
  calcTotals();
}

function invalidatePaymentReview(){if(posStep==='books'&&!pendingSalePayload){settlementMode=null;reviewedTotalCents=null;document.getElementById('paid').value='0';}}
function unitPriceHTML(item){const unit=amountCents(itemNet(item)/item.qty);const exact=unit*item.qty===amountCents(itemNet(item));return (exact?'':'<small class="approximate-price" title="পয়সা মিলিয়ে সঠিক মোট টাকা পাশের ঘরে দেখানো হয়েছে।">প্রায় </small>')+'৳'+money(unit/100);}
function renderCart() {
  const el=document.getElementById('cart-scroll');if(!el)return;
  setText('cart-count-lbl',cart.length ? money(cart.length)+' ধরনের বই খাতায় লেখা হয়েছে' : 'এখনো কোনো বই লেখা হয়নি');
  if(!cart.length)el.innerHTML=`<tr><td colspan="5" class="ledger-empty"><span>${icon('book',36)}</span><h3>বইয়ের নাম দিয়ে শুরু করুন</h3><p>উপরের ঘরে বই খুঁজে বাছুন। এখানে নাম, পরিমাণ ও দাম নিজে বসবে।</p></td></tr>`;
  else el.innerHTML=cart.map((i,index)=>`<tr class="${i.qty>i.max?'stock-invalid':''}"><td class="ledger-book"><div class="ledger-book-content"><span class="line-number">${money(index+1)}</span><div><strong>${escapeHTML(i.title)}</strong>${i.qty>i.max?'<small class="stock-error">দোকানে '+money(i.max)+' কপি আছে। পরিমাণ ঠিক করুন অথবা বইটি মুছুন।</small>':''}<div class="line-adjust"><label for="extra-${i.bid}">বাড়তি ছাড় %</label><input id="extra-${i.bid}" type="number" min="0" max="100" step="0.01" value="${i.extraCommission||0}" onchange="setExtraCommission(${i.bid},this.value);renderCart()"></div></div></div></td><td data-label="পরিমাণ"><div class="ci-qty"><button type="button" id="minus-${i.bid}" class="qb" aria-label="এক কপি কমান" ${i.qty<=1?'disabled':''} onclick="changeQty(${i.bid},-1)">−</button><input id="qty-${i.bid}" aria-label="${escapeHTML(i.title)} পরিমাণ" class="ci-count-input" type="text" inputmode="numeric" value="${i.qty}" onchange="setQty(${i.bid},this.value)" onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur();document.getElementById('book-search').focus()}"><button type="button" id="plus-${i.bid}" class="qb" aria-label="এক কপি বাড়ান" ${i.qty>=i.max?'disabled':''} onclick="changeQty(${i.bid},1)">+</button></div></td><td data-label="প্রতি বইয়ের দাম" class="ledger-unit" id="unit-${i.bid}">${unitPriceHTML(i)}</td><td data-label="মোট টাকা" class="ledger-line-total" id="line-total-${i.bid}">৳${money(itemNet(i))}</td><td class="ledger-remove"><button type="button" aria-label="${escapeHTML(i.title)} মুছুন" onclick="removeItem(${i.bid})">${icon('close',18)}</button></td></tr>`).join('');
  filterBooks();saveCartDraft();
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
  document.getElementById('wholesale-search').value = `${c.name} (${c.customer_code||c.phone})`;
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
  if(pendingSalePayload||saleSubmitting)return;
  const lookup = ++dueLookupId;
  const phone=normalizeDigits(document.getElementById('cus-phone')?.value).replace(/[\s-]/g,'');
  const row        = document.getElementById('prev-due-row');
  const val        = document.getElementById('t-prev-due');
  const indicator  = document.getElementById('phone-check-indicator');
  currentCustomerDue = 0;
  if (indicator) indicator.innerHTML = '';

  if (phone && phone.length >= 11) {
    try {
      const res = await api(`/sales/customers/find_by_phone/?phone=${encodeURIComponent(phone)}`);
      if (lookup !== dueLookupId || normalizeDigits(document.getElementById('cus-phone').value).replace(/[\s-]/g,'') !== phone) return;
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

function normalizeDigits(value) {return String(value??'').replace(/[০-৯]/g,c=>'০১২৩৪৫৬৭৮৯'.indexOf(c));}
function inputCents(value) {
  let text=normalizeDigits(value).trim();
  if(text==='')return null;
  if(text.includes(',')) {
    if(!/^(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d{1,2})?$/.test(text))return null;
    text=text.replace(/,/g,'');
  }
  if(!/^(?:\d+(?:\.\d{1,2})?|\.\d{1,2})$/.test(text))return null;
  const [whole,fraction='']=text.split('.');
  const cents=Number(whole||0)*100+Number(fraction.padEnd(2,'0'));
  return Number.isSafeInteger(cents)&&cents<=999999999999?cents:null;
}
function centsInput(cents) {return (cents/100).toFixed(2);}
function amountCents(value) {return Math.round(Number(value||0)*100);}
function ledgerAmounts() {
  const gross=cart.reduce((sum,i)=>sum+amountCents(i.price)*i.qty,0);
  const discount=cart.reduce((sum,i)=>sum+amountCents(itemDiscount(i)),0);
  const extra=['pack','courier','txfee'].reduce((sum,id)=>sum+amountCents(document.getElementById(id)?.value),0);
  return {gross,discount,extra,total:Math.max(0,gross-discount+extra)};
}
function billCents() {return ledgerAmounts().total;}
function currentBillTotal() {return billCents()/100;}
function setText(id,value) {const el=document.getElementById(id);if(el)el.textContent=value;}
function calcTotals(manualDisc=false) {
  const sub=cart.reduce((sum,i)=>sum+itemGross(i),0);
  if(manualDisc&&sub>0){
    document.getElementById('commission').value=Math.min(100,(Number(document.getElementById('discount').value||0)/sub)*100).toFixed(6);
    renderCart();
  }
  if(!manualDisc)document.getElementById('discount').value=(sub*defaultCommissionPct()/100).toFixed(2);
  const amounts=ledgerAmounts(),qty=cart.reduce((sum,i)=>sum+i.qty,0);
  if(posStep==='payment'&&!pendingSalePayload&&reviewedTotalCents!==null&&reviewedTotalCents!==amounts.total){
    settlementMode=null;document.getElementById('paid').value='0';reviewedTotalCents=amounts.total;
    setText('finish-help','বিলের অঙ্ক বদলেছে। টাকা পেয়েছেন কি না আবার বাছুন।');
  }
  setText('cart-total','৳'+money(amounts.total/100));setText('t-total','৳'+money(amounts.total/100));
  setText('t-items',money(cart.length));setText('t-qty',money(qty));
  setText('checkout-count',money(qty)+' কপি বই · দাম ও ছাড় হিসাব করা আছে');
  setText('t-sub','৳'+money(amounts.gross/100));setText('t-disc','−৳'+money(amounts.discount/100));
  setText('t-extra','৳'+money(amounts.extra/100));setText('review-total','৳'+money(amounts.total/100));
  const review=document.getElementById('review-lines');
  if(review)review.innerHTML=cart.map(i=>`<div class="review-line"><span>${escapeHTML(i.title)}<small>${money(i.qty)} কপি</small></span><b>৳${money(itemNet(i))}</b></div>`).join('');
  setText('discount-explanation',document.getElementById('sale-type').value==='wholesale'?'পাইকারি কমিশন বইয়ের স্বাভাবিক ছাড়ের পরিবর্তে ব্যবহৃত হয়। কমিশন ০ হলে বইয়ের স্বাভাবিক ছাড় থাকবে।':'খুচরা বিক্রয়ে এখানে দেওয়া ছাড় বইয়ের স্বাভাবিক ছাড়ের সঙ্গে যোগ হবে।');
  setText('adjustments-indicator',amounts.extra>0||defaultCommissionPct()>0?'বিশেষ ছাড় / বাড়তি খরচ এই হিসাবে যোগ করা আছে।':'');
  document.getElementById('checkout-btn').disabled=!cart.length||saleSubmitting||catalogLoading||!!pendingSalePayload||cart.some(i=>i.qty>i.max);
  renderPaymentState();saveCartDraft();
}
function chooseSettlement(mode) {
  if(saleSubmitting||pendingSalePayload)return;
  settlementMode=mode;reviewedTotalCents=billCents();posStep='payment';document.getElementById('checkout-error').hidden=true;
  const method=document.getElementById('pay-method');
  if(mode==='due'){method.value='credit';document.getElementById('paid').value='0';}
  else {if(method.value==='credit')method.value='cash';document.getElementById('paid').value=mode==='full'?centsInput(billCents()):'';}
  renderPaymentState();saveCartDraft();
  if(mode==='partial')document.getElementById('paid').focus();
  if(mode==='due')document.getElementById('cus-name').focus();
}
function choosePaymentMethod(method) {
  if(saleSubmitting||pendingSalePayload||settlementMode==='due')return;
  document.getElementById('pay-method').value=method;renderPaymentState();saveCartDraft();
}
function toggleCustomerDetails() {
  if(saleSubmitting||pendingSalePayload)return;
  customerDetailsOpen=!customerDetailsOpen;renderPaymentState();
  if(customerDetailsOpen)document.getElementById('cus-name').focus();
}
function renderPaymentState() {
  const total=billCents(),received=inputCents(document.getElementById('paid').value);
  const paid=settlementMode==='due'?0:received??0;
  const due=Math.max(0,total-paid),change=Math.max(0,paid-total);
  const method=document.getElementById('pay-method').value;
  ['full','partial','due'].forEach(mode=>document.getElementById('mode-'+mode).setAttribute('aria-pressed',String(settlementMode===mode)));
  ['cash','mobile','bank'].forEach(value=>document.getElementById('method-'+value).setAttribute('aria-pressed',String(method===value)));
  document.getElementById('payment-entry').hidden=false;
  document.getElementById('payment-methods').hidden=false;
  document.getElementById('received-field').hidden=false;
  document.getElementById('paid').disabled=!settlementMode||settlementMode==='due'||saleSubmitting||!!pendingSalePayload;
  ['cash','mobile','bank'].forEach(value=>document.getElementById('method-'+value).disabled=settlementMode==='due'||saleSubmitting||!!pendingSalePayload);
  const needsCustomer=settlementMode==='partial'||settlementMode==='due';
  const wholesale=document.getElementById('sale-type').value==='wholesale';
  document.getElementById('customer-area').hidden=false;
  document.getElementById('customer-toggle').hidden=true;
  setText('customer-toggle',customerDetailsOpen?'ক্রেতার তথ্য লুকান':'বিলে ক্রেতার নাম / ফোন লিখতে চান?');
  setText('customer-heading',needsCustomer?'কার নামে বাকি রাখবেন?':'ক্রেতার তথ্য');
  setText('customer-help',needsCustomer?'নাম ও ফোন দিলে বাকি সঠিক ক্রেতার হিসাবে থাকবে।':'এই তথ্য দেওয়া ঐচ্ছিক।');
  ['cus-name','cus-phone'].forEach(id=>document.getElementById(id).required=needsCustomer);
  setText('paid-label',settlementMode==='due'?'এখন টাকা নেওয়া হয়নি':settlementMode==='partial'?'কত টাকা পেয়েছেন?':method==='cash'?'ক্রেতা কত নগদ দিয়েছেন?':'কত টাকা এসেছে?');
  setText('payment-input-hint',settlementMode==='due'?'এখন টাকা জমা হবে না। ক্রেতার নাম ও ফোন লিখে বাকি রাখুন।':settlementMode==='partial'?'যত টাকা পেয়েছেন শুধু সেটুকু লিখুন। বাকি নিজে হিসাব হবে।':method==='cash'?'বেশি নগদ দিলে সেই অঙ্ক লিখুন। কত ফেরত দেবেন নিচে দেখাবে।':'যে টাকা অ্যাকাউন্টে এসেছে শুধু সেই অঙ্ক লিখুন।');
  const shortcuts=document.getElementById('cash-shortcuts');
  shortcuts.hidden=method!=='cash'||settlementMode!=='full';
  let notes=[50,100,200,500,1000,2000,5000].filter(n=>n*100>=total).slice(0,3);
  if(!notes.length)notes=[Math.ceil(total/100000)*1000];
  shortcuts.innerHTML=`<button type="button" onclick="setPaidFull()">ঠিক বিলের টাকা</button>`+notes.map(n=>`<button type="button" onclick="setPaidAmount(${n})">৳${money(n)} দিয়েছেন</button>`).join('');
  const outcome=document.getElementById('payment-outcome');outcome.className='payment-outcome';
  let label,amount,help;
  if(!settlementMode){label='টাকা পেয়েছেন কি না বাছুন';amount='৳'+money(total/100);help='উপরে পুরো টাকা, কিছু টাকা অথবা বাকি—যেটি ঠিক সেটি বাছুন।';}
  else if(received===null&&settlementMode!=='due') {label='পাওয়া টাকার অঙ্ক লিখুন';amount='—';help='বাংলা বা ইংরেজি সংখ্যা লিখতে পারবেন।';}
  else if(change>0&&method!=='cash'){label='অঙ্কটি আবার দেখুন';amount='৳'+money(change/100)+' বেশি';help='মোবাইল বা ব্যাংকের বাড়তি টাকা এই বিলে জমা হবে না। সঠিক অঙ্ক লিখুন।';outcome.classList.add('invalid');}
  else if(settlementMode==='partial'&&(paid<=0||paid>=total)){label='কিছু টাকা পেয়েছেন?';amount='—';help='বিলের চেয়ে কম, শূন্যের বেশি টাকা লিখুন। পুরো টাকা পেলে উপরে প্রথম বোতামটি চাপুন।';outcome.classList.add('invalid');}
  else if(settlementMode==='full'&&paid<total){label='আরও টাকা নেওয়া বাকি';amount='৳'+money(due/100);help='কিছু টাকা পেয়ে থাকলে “কিছু টাকা পেয়েছি” বাছুন।';outcome.classList.add('invalid');}
  else if(change>0){label='ক্রেতাকে ফেরত দিন';amount='৳'+money(change/100);help='বিলে জমা হবে ৳'+money(total/100)+'। বাড়তি টাকা ফেরত দিন।';outcome.classList.add('change');}
  else if(due>0){label='এই বিলে বাকি থাকবে';amount='৳'+money(due/100);help=paid>0?'পেয়েছেন ৳'+money(paid/100)+'। বাকি ক্রেতার হিসাবে থাকবে।':'এখন কোনো টাকা নেওয়া হয়নি।';outcome.classList.add('due');}
  else {label='সব টাকা পেয়েছেন';amount='বাকি নেই';help='কোনো টাকা ফেরত দিতে হবে না।';outcome.classList.add('settled');}
  setText('outcome-label',label);setText('outcome-amount',amount);setText('outcome-help',help);
  setText('recorded-paid','৳'+money(Math.min(total,paid)/100));setText('t-due','৳'+money(due/100));
  setText('t-grand-due','৳'+money(currentCustomerDue+due/100));document.getElementById('grand-due-row').hidden=currentCustomerDue<=0;
  const btn=document.getElementById('complete-btn');
  const valid=settlementMode==='due'||(received!==null&&(settlementMode==='full'?paid>=total&&(method==='cash'||paid===total):settlementMode==='partial'&&paid>0&&paid<total));
  btn.disabled=saleSubmitting||catalogLoading||!cart.length||(!pendingSalePayload&&(cart.some(i=>i.qty>i.max)||!valid));
  const methodLabel={cash:'নগদ',mobile:'মোবাইল',bank:'ব্যাংকে',credit:'বাকি'}[method]||'';
  btn.textContent=saleSubmitting?'বিক্রয় সেভ হচ্ছে…':pendingSalePayload?'আগের বিক্রয় সেভ হয়েছে কি না যাচাই করুন':!settlementMode?'আগে টাকা পেয়েছেন কি না বাছুন':!valid?'উপরের টাকার অঙ্ক ঠিক করুন':settlementMode==='due'?'৳'+money(total/100)+' বাকি রেখে বিক্রয় শেষ করুন':'বিক্রয় শেষ করুন · '+methodLabel+' ৳'+money(Math.min(total,paid)/100)+' জমা';
}
function onPaidInput() {document.getElementById('checkout-error').hidden=true;renderPaymentState();saveCartDraft();}
function setPaidFull() {if(saleSubmitting||pendingSalePayload)return;document.getElementById('paid').value=centsInput(billCents());renderPaymentState();saveCartDraft();}
function setPaidAmount(value) {if(saleSubmitting||pendingSalePayload)return;document.getElementById('paid').value=String(value);renderPaymentState();saveCartDraft();}
function onPayMethodChange() {renderPaymentState();saveCartDraft();}

function checkoutError(text,field) {
  const el = document.getElementById('checkout-error');
  el.textContent = text; el.hidden = false;
  if (field) field.focus(); else el.focus();
}
async function sendCheckout(payload) {
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);
  try {
    const response=await fetch(API+'/sales/',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':getCookie('csrftoken')},body:JSON.stringify(payload),signal:controller.signal});
    const data=await response.json().catch(()=>null);
    if(!response.ok||!data||!data.id||data.total===undefined){const error=new Error(data?formatApiError(data):'সার্ভারের উত্তর পড়া যায়নি।');error.uncertain=response.status>=500||[408,409,425,429].includes(response.status)||response.ok;throw error;}
    return data;
  } catch(error){if(error.uncertain===undefined)error.uncertain=true;throw error;}
  finally {clearTimeout(timer);}
}
function lockPendingCheckout() {
  document.querySelectorAll('.sales-desk input,.sales-desk select,.sales-desk button').forEach(el=>{el.disabled=true;});
  document.getElementById('complete-btn').disabled=saleSubmitting;
}
async function completeSale() {
  if(saleSubmitting||catalogLoading||(!cart.length&&!pendingSalePayload))return;
  document.getElementById('checkout-error').hidden=true;
  let payload=pendingSalePayload,received=pendingReceived;
  if(!payload){
    if(!settlementMode){checkoutError('টাকা পেয়েছেন কি না আগে বাছুন।',document.getElementById('mode-full'));return;}
    for(const id of ['commission','discount','pack','courier','txfee']){
      const el=document.getElementById(id);
      if(!Number.isFinite(Number(el.value))||Number(el.value)<0||!el.checkValidity()){
        showCartStep();
        checkoutError('বিশেষ ছাড় বা বাড়তি খরচের অঙ্ক ঠিক করুন।',el);return;
      }
    }
    const total=billCents(),paid=settlementMode==='due'?0:inputCents(document.getElementById('paid').value);
    const method=document.getElementById('pay-method').value;
    if(paid===null){checkoutError('পাওয়া টাকার অঙ্ক লিখুন।',document.getElementById('paid'));return;}
    if(settlementMode==='full'&&paid<total){checkoutError('পুরো টাকা পাননি। “কিছু টাকা পেয়েছি” অথবা “বাকি থাকবে” বাছুন।');return;}
    if(settlementMode==='partial'&&(paid<=0||paid>=total)){checkoutError('আংশিক পেমেন্টে বিলের চেয়ে কম, শূন্যের বেশি টাকা লিখুন।',document.getElementById('paid'));return;}
    if(method!=='cash'&&paid>total){checkoutError('মোবাইল বা ব্যাংকে বিলের চেয়ে বেশি টাকা জমা হবে না। সঠিক অঙ্ক লিখুন।',document.getElementById('paid'));return;}
    if((settlementMode==='due')!==(method==='credit')){checkoutError('পেমেন্টের ধরন আবার বাছুন।');return;}
    const name=document.getElementById('cus-name').value.trim();
    const phone=normalizeDigits(document.getElementById('cus-phone').value).replace(/[\s-]/g,'');
    if(paid<total&&(!name||!phone)){checkoutError('কার নামে বাকি থাকবে? ক্রেতার নাম ও ফোন লিখুন।',document.getElementById(!name?'cus-name':'cus-phone'));return;}
    if(phone&&!/^\+?\d{7,15}$/.test(phone)){checkoutError('ক্রেতার সঠিক ফোন নম্বর লিখুন।',document.getElementById('cus-phone'));return;}
    for(const item of cart){if(!Number.isInteger(item.qty)||item.qty<1||item.qty>item.max){showCartStep();checkoutError('বইয়ের পরিমাণ দোকানের স্টকের মধ্যে লিখুন।');return;}}
    clearTimeout(phoneCheckTimer);dueLookupId++;
    checkoutRequestId=crypto.randomUUID();
    payload={request_id:checkoutRequestId,customer_name:name||'Walk-in Customer',customer_phone:phone,
      discount:'0.00',packaging_charge:centsInput(amountCents(document.getElementById('pack').value)),
      courier_charge:centsInput(amountCents(document.getElementById('courier').value)),transaction_fee:centsInput(amountCents(document.getElementById('txfee').value)),
      paid_amount:centsInput(Math.min(paid,total)),payment_method:method,sale_type:document.getElementById('sale-type').value,
      items:cart.map(i=>({book:i.bid,quantity:i.qty,unit_price:centsInput(amountCents(i.price)),discount:centsInput(amountCents(itemDiscount(i)))}))};
    received=paid;pendingSalePayload=payload;pendingReceived=paid;saveCartDraft();
  }
  saleSubmitting=true;renderPaymentState();lockPendingCheckout();
  let sale;
  try {sale=await sendCheckout(payload);}
  catch(error){
    saleSubmitting=false;
    if(!error.uncertain){
      pendingSalePayload=null;checkoutRequestId=null;
      document.querySelectorAll('.sales-desk input,.sales-desk select,.sales-desk button').forEach(el=>{el.disabled=false;});
      try{allBooks=await loadAllBooks();cart.forEach(i=>{i.max=Number(allBooks.find(b=>b.id===i.bid)?.shop_quantity)||0;});}catch(_){}
      if(cart.some(i=>i.qty>i.max)){showCartStep();renderCart();calcTotals();}
    }
    renderPaymentState();if(pendingSalePayload)lockPendingCheckout();saveCartDraft();
    checkoutError(error.uncertain?'সেভ হয়েছে কি না নিশ্চিত করা যায়নি। এই পেজ খোলা রাখুন এবং নিচের যাচাই বোতাম চাপুন। একই বিল দুবার জমা হবে না।':error.message||formatApiError(error));
    return;
  }
  cart=[];currentCustomerDue=0;settlementMode=null;customerDetailsOpen=false;reviewedTotalCents=null;
  pendingSalePayload=null;checkoutRequestId=null;pendingReceived=0;clearCartDraft();
  try {allBooks=await loadAllBooks();}
  catch(_){allBooks.forEach(b=>{const i=payload.items.find(i=>i.book===b.id);if(i)b.shop_quantity=Math.max(0,Number(b.shop_quantity)-i.quantity);});}
  saleSubmitting=false;renderPOS(document.getElementById('pos-root'));
  showSuccessModal({...sale,cash_received:received/100,change_due:Math.max(0,received-amountCents(sale.total))/100});
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

document.addEventListener('pointerdown',event=>{if(!event.target.closest('.ledger-search-wrap')&&document.getElementById('book-picker'))closeBookPicker();});
document.addEventListener('DOMContentLoaded', initPOS);
