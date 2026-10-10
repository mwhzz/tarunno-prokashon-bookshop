(() => {
  'use strict';
  const root = document.querySelector('.book-editor');
  if (!root) return;
  const form = document.getElementById('book-edit-form');
  const state = document.getElementById('editor-state');
  const message = document.getElementById('editor-message');
  const save = document.getElementById('save-edit');
  const reset = document.getElementById('reset-edit');
  const imageInput = document.getElementById('edit-image');
  const preview = document.getElementById('cover-preview');
  const undoImage = document.getElementById('undo-image');
  const retry = document.getElementById('retry-editor');
  const moneyFields = new Set(['purchase_price', 'selling_price', 'mrp', 'commission', 'discount']);
  let book, original = {}, saving = false, imageFile = null, objectURL = null;
  const number = value => new Intl.NumberFormat('bn-BD', {maximumFractionDigits: 2}).format(Number(value) || 0);
  const money = value => '৳ ' + number(value);
  const values = () => Object.fromEntries([...form.elements].filter(el => el.name).map(el => [el.name, el.value]));
  const same = (key, a, b) => moneyFields.has(key) ? Number(a) === Number(b) : a === b;
  const changed = () => !!imageFile || Object.entries(values()).some(([key, value]) => !same(key, value, original[key]));
  function showMessage(text) { message.textContent = text; message.hidden = false; message.focus(); }
  function clearErrors() {
    message.hidden = true;
    root.querySelectorAll('[data-error]').forEach(el => { el.textContent = ''; });
    form.querySelectorAll('[aria-invalid]').forEach(el => el.removeAttribute('aria-invalid'));
  }
  function showCover(url) {
    preview.hidden = !url;
    document.getElementById('cover-placeholder').hidden = !!url;
    if (url) preview.src = url;
    else preview.removeAttribute('src');
  }
  preview.addEventListener('error', () => {
    preview.hidden = true;
    const placeholder = document.getElementById('cover-placeholder');
    placeholder.hidden = false;
    placeholder.textContent = 'প্রচ্ছদ দেখা যাচ্ছে না';
  });
  function clearImage() {
    if (objectURL) URL.revokeObjectURL(objectURL);
    objectURL = null; imageFile = null; imageInput.value = '';
    undoImage.hidden = true;
    document.getElementById('image-name').textContent = '';
    showCover(book.image);
  }
  function update() {
    const dirty = changed();
    save.disabled = saving || !dirty;
    reset.disabled = saving || !dirty;
    state.textContent = saving ? 'সেভ হচ্ছে…' : dirty ? 'পরিবর্তন সেভ হয়নি' : 'সব তথ্য সেভ আছে';
    document.getElementById('save-caption').textContent = saving ? 'অনুগ্রহ করে অপেক্ষা করুন' : dirty ? 'পরিবর্তনগুলো সেভ করুন' : 'সব তথ্য আপডেট আছে';
    const v = values(), cost = Number(v.purchase_price) || 0, price = Number(v.selling_price) || 0;
    const stock = Number(book.current_stock) || 0;
    document.getElementById('stock-cost').textContent = money(cost * stock);
    document.getElementById('stock-retail').textContent = money(price * stock);
    const note = document.getElementById('price-note');
    note.classList.toggle('warning', price < cost || (cost === 0 && stock > 0));
    note.textContent = price < cost ? 'সতর্কতা: বিক্রয়মূল্য কস্টিংয়ের চেয়ে কম।' : cost === 0 && stock > 0 ? 'কস্টিং দেওয়া নেই। সঠিক স্টক হিসাবের জন্য প্রতি কপির খরচ লিখুন।' : 'প্রতি কপিতে দামের পার্থক্য ' + money(price - cost) + ' — কমিশন ও ডিসকাউন্টের আগে।';
    form.elements.discount.max = v.discount_type === 'percentage' ? '100' : '99999999.99';
  }
  function populate(data) {
    book = data;
    [...form.elements].filter(el => el.name).forEach(el => {
      el.value = el.name === 'is_active' ? String(data.is_active) : (data[el.name] ?? '');
    });
    original = values(); clearImage(); clearErrors();
    document.getElementById('stock-total').textContent = number(book.current_stock) + ' কপি';
    document.getElementById('stock-shop').textContent = number(book.shop_quantity);
    document.getElementById('stock-godown').textContent = number(book.godown_quantity);
    update();
  }
  async function load() {
    retry.hidden = true; clearErrors(); state.textContent = 'তথ্য লোড হচ্ছে…';
    try {
      const response = await fetch(API + '/books/' + root.dataset.bookId + '/', {cache: 'no-store'});
      if (!response.ok) throw new Error(response.status === 404 ? 'বইটি পাওয়া যায়নি। বইয়ের তালিকায় ফিরে দেখুন।' : 'বইয়ের তথ্য লোড করা যায়নি। আবার চেষ্টা করুন।');
      populate(await response.json()); form.hidden = false;
    } catch (error) {
      state.textContent = 'তথ্য লোড হয়নি';
      showMessage(error instanceof TypeError ? 'সার্ভারের সঙ্গে সংযোগ হয়নি। ইন্টারনেট চেক করে আবার চেষ্টা করুন।' : error.message);
      retry.hidden = false;
    }
  }
  form.addEventListener('input', event => {
    const field = event.target;
    if (field.name) {
      field.removeAttribute('aria-invalid');
      const error = root.querySelector('[data-error="' + CSS.escape(field.name) + '"]');
      if (error) error.textContent = '';
    }
    update();
  });
  form.addEventListener('change', update);
  form.addEventListener('invalid', event => {
    event.target.setAttribute('aria-invalid', 'true');
    const error = root.querySelector('[data-error="' + event.target.name + '"]');
    if (error) error.textContent = event.target.validity.valueMissing ? 'এই তথ্যটি লিখুন।' : 'সঠিক মান লিখুন (সীমা: ' + (event.target.min || '০') + '–' + (event.target.max || 'নির্ধারিত দৈর্ঘ্য') + ')।';
    const details = event.target.closest('details');
    if (details) details.open = true;
  }, true);
  imageInput.addEventListener('change', () => {
    const file = imageInput.files[0]; if (!file) return;
    if (!['image/jpeg','image/png','image/webp','image/gif'].includes(file.type)) {
      imageInput.value = ''; showMessage('প্রচ্ছদের জন্য JPG, PNG, WEBP অথবা GIF ছবি নির্বাচন করুন।'); return;
    }
    if (objectURL) URL.revokeObjectURL(objectURL);
    imageFile = file; objectURL = URL.createObjectURL(file); showCover(objectURL);
    document.getElementById('image-name').textContent = file.name;
    undoImage.hidden = false; update();
  });
  undoImage.addEventListener('click', () => { clearImage(); update(); });
  reset.addEventListener('click', async () => {
    if (saving || !changed()) return;
    if (await confirmModal('সেভ না হওয়া পরিবর্তনগুলো বাতিল করবেন?')) populate(book);
  });
  root.querySelector('.editor-back').addEventListener('click', async event => {
    if (!book || !changed()) return;
    event.preventDefault();
    if (saving) return;
    if (await confirmModal('পরিবর্তন সেভ হয়নি। সেভ না করেই বইয়ের তালিকায় ফিরবেন?')) {
      original = values(); imageFile = null; window.location.href = '/books/';
    }
  });
  window.addEventListener('beforeunload', event => {
    if (book && changed()) { event.preventDefault(); event.returnValue = ''; }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (saving || !changed()) return;
    clearErrors();
    if (!form.elements.title.value.trim()) {
      showMessage('বইয়ের নাম লিখুন।'); form.elements.title.focus(); return;
    }
    saving = true; update();
    const payload = new FormData();
    Object.entries(values()).forEach(([key, value]) => {
      if (!same(key, value, original[key])) payload.append(key, value);
    });
    if (imageFile) payload.append('image', imageFile);
    [...form.elements].forEach(el => { el.disabled = true; });
    try {
      const response = await fetch(API + '/books/' + root.dataset.bookId + '/', {
        method: 'PATCH', headers: {'X-CSRFToken': getCookie('csrftoken')}, body: payload
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        let first;
        Object.entries(data).forEach(([key, errors]) => {
          const field = form.elements.namedItem(key);
          const target = root.querySelector('[data-error="' + CSS.escape(key) + '"]');
          if (target) target.textContent = Array.isArray(errors) ? errors.join(' ') : String(errors);
          if (field) { field.setAttribute('aria-invalid', 'true'); if (field.closest('details')) field.closest('details').open = true; first ||= field; }
        });
        showMessage(response.status === 413 ? 'ছবিটি বেশি বড়। ছোট আকারের ছবি দিয়ে আবার চেষ্টা করুন।' : 'পরিবর্তন সেভ হয়নি। চিহ্নিত তথ্য ঠিক করে আবার সেভ করুন।');
        if (first) setTimeout(() => first.focus(), 0);
        return;
      }
      populate(data);
      state.textContent = 'সফলভাবে সেভ হয়েছে';
      document.getElementById('save-caption').textContent = 'বইয়ের তথ্য সফলভাবে সেভ হয়েছে';
    } catch (error) {
      showMessage('সেভ নিশ্চিত করা যায়নি। সংযোগ চেক করে আবার চেষ্টা করুন। আপনার লেখা তথ্য এই পেজে আছে।');
    } finally {
      saving = false;
      [...form.elements].forEach(el => { el.disabled = false; });
      save.disabled = !changed(); reset.disabled = !changed();
      if (changed()) update();
    }
  });
  retry.addEventListener('click', load);
  load();
})();
