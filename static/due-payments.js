
async function initDue() {
  const w = document.getElementById('due-wrapper');
  try {
    // API থেকে দুই ধরনের বাকি আসছে: (১) নির্দিষ্ট ইনভয়েসের বাকি, (২) গ্রাহকের এমন বাকি
    // যা কোনো ইনভয়েসের সাথে যুক্ত নয় (যেমন গ্রাহক যোগ করার সময়ের ওপেনিং ব্যালেন্স)
    const r = await api('/sales/due_list/');
    const invoices     = r.invoices || [];
    const customerDues = r.customer_dues || [];

    // মোট কত টাকা বাকি আছে তার হিসাব (দুই ধরন মিলিয়ে)
    const invoiceDueTotal = invoices.reduce((sum, item) => sum + parseFloat(item.due_amount), 0);
    const otherDueTotal   = customerDues.reduce((sum, item) => sum + parseFloat(item.amount), 0);
    const totalDue        = invoiceDueTotal + otherDueTotal;

    w.innerHTML = `
      <div class="stat-grid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px;">
        <div class="stat-card red">
            <div class="stat-label">মোট বাকি বিল (Total Due Invoices)</div>
            <div class="stat-value">${invoices.length}টি</div>
        </div>
        <div class="stat-card red">
            <div class="stat-label">মোট বাকি পরিমাণ (Total Due Amount)</div>
            <div class="stat-value">৳${num(totalDue)}</div>
        </div>
      </div>

      <div class="table-wrap">
        <div class="table-header">
            <span class="table-title">${icon('alert-triangle', 15)} বাকি ইনভয়েসের তালিকা</span>
        </div>
        <table style="width:100%; border-collapse:collapse;">
          <thead>
            <tr>
                <th>ইনভয়েস নম্বর</th>
                <th>গ্রাহক</th>
                <th>ফোন</th>
                <th>মোট বিল</th>
                <th>বাকি পরিমাণ</th>
                <th>স্ট্যাটাস</th>
                <th>তারিখ</th>
                <th>অ্যাকশন</th>
            </tr>
          </thead>
          <tbody>
            ${invoices.length ? invoices.map(s => `<tr>
              <td style="font-family:AppBangla,sans-serif"><strong>${s.invoice_number}</strong></td>
              <td><strong>${s.customer_name}</strong></td>
              <td style="color:var(--muted)">${s.customer_phone || '—'}</td>
              <td style="font-family:AppBangla,sans-serif">৳${num(s.total)}</td>
              <td style="font-family:AppBangla,sans-serif;color:var(--danger);font-weight:700">৳${num(s.due_amount)}</td>
              <td>${statusBadge(s.status)}</td>
              <td style="color:var(--muted);font-size:16px">${fmtDate(s.created_at)}</td>
              <td style="white-space:nowrap">
                <button class="btn btn-success btn-sm" onclick="collectPayment(${s.id}, ${s.due_amount})">
                  ৳ পেমেন্ট নিন
                </button>
                <a class="btn btn-ghost btn-sm" href="/api/sales/${s.id}/print_invoice/" target="_blank" style="text-decoration:none">${icon('printer', 13)} রশিদ</a>
              </td>
            </tr>`).join('') : `<tr><td colspan="8" class="empty">কোনো বাকি ইনভয়েস নেই।</td></tr>`}
          </tbody>
        </table>
      </div>

      ${customerDues.length ? `
      <div class="table-wrap" style="margin-top:20px">
        <div class="table-header">
            <span class="table-title">${icon('alert-triangle', 15)} অন্যান্য বাকি (ইনভয়েসের বাইরে — যেমন ওপেনিং ব্যালেন্স)</span>
        </div>
        <table style="width:100%; border-collapse:collapse;">
          <thead>
            <tr>
                <th>কোড</th>
                <th>গ্রাহক</th>
                <th>ফোন</th>
                <th>বাকি পরিমাণ</th>
                <th>অ্যাকশন</th>
            </tr>
          </thead>
          <tbody>
            ${customerDues.map(c => `<tr>
              <td style="font-family:AppBangla,sans-serif">${c.customer_code || '—'}</td>
              <td><strong>${c.name}</strong></td>
              <td style="color:var(--muted)">${c.phone || '—'}</td>
              <td style="font-family:AppBangla,sans-serif;color:var(--danger);font-weight:700">৳${num(c.amount)}</td>
              <td>
                <button class="btn btn-success btn-sm" onclick="collectCustomerDue(${c.id}, ${c.amount})">
                  ৳ পেমেন্ট নিন
                </button>
              </td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>` : ''}`;
  } catch(e) {
      w.innerHTML = '<div class="empty">ডাটা লোড করতে সমস্যা হয়েছে।</div>';
  }
}


const DUE_PENDING_KEY='due_payment_pending_v1';
let dueAttempt=null, duePaymentBusy=false;
function dueCents(value) {
  const text=String(value??'').trim().replace(/[০-৯]/g,c=>'০১২৩৪৫৬৭৮৯'.indexOf(c));
  if(!/^\d+(?:\.\d{1,2})?$/.test(text))return null;
  const [whole,fraction='']=text.split('.'), cents=Number(whole)*100+Number(fraction.padEnd(2,'0'));
  return Number.isSafeInteger(cents)&&cents<=999999999999?cents:null;
}
function dueDecimal(cents){return (cents/100).toFixed(2);}
function dueError(text){const el=document.getElementById('due-payment-error');if(el){el.textContent=text;el.hidden=false;}}
function updatePayPreview(maxAmount) {
  const amount=dueCents(document.getElementById('pay-amount')?.value),discount=dueCents(document.getElementById('pay-discount')?.value);
  const el=document.getElementById('pay-remaining');if(!el)return;
  el.textContent=amount===null||discount===null?'সঠিক অঙ্ক লিখুন':'৳'+num(Math.max(0,Math.round(maxAmount*100)-amount-discount)/100);
}
function closeWithDiscount(maxAmount) {
  if(duePaymentBusy||dueAttempt?.payload)return;
  const amount=dueCents(document.getElementById('pay-amount').value);if(amount===null)return;
  document.getElementById('pay-discount').value=dueDecimal(Math.max(0,Math.round(maxAmount*100)-amount));updatePayPreview(maxAmount);
}
function collectPayment(id,maxAmount){openDuePayment('sale',id,maxAmount);}
function collectCustomerDue(id,maxAmount){openDuePayment('customer',id,maxAmount);}
function dismissDuePayment(){
  if(duePaymentBusy||dueAttempt?.payload){dueError('আগের পেমেন্ট জমা হয়েছে কি না আগে যাচাই করুন।');return;}
  document.getElementById('payment-modal')?.remove();dueAttempt=null;
}
function openDuePayment(kind,id,maxAmount) {
  if(duePaymentBusy)return;
  if(!dueAttempt?.payload)dueAttempt={kind,id,maxAmount:Number(maxAmount),payload:null};
  document.getElementById('payment-modal')?.remove();
  const attempt=dueAttempt, invoice=attempt.kind==='sale',max=Number(attempt.maxAmount);
  document.body.insertAdjacentHTML('beforeend',`
  <div id="payment-modal" style="position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;z-index:1000;padding:18px">
    <div class="form-card" role="dialog" aria-modal="true" aria-labelledby="due-payment-title" style="width:440px;max-width:100%;max-height:90vh;overflow:auto;padding:24px">
      <h3 id="due-payment-title" style="margin-bottom:12px">${icon('wallet',18)} বাকি টাকা জমা নিন</h3>
      <p style="font-size:17px;margin-bottom:18px">${invoice?'এই বিলের বাকি':'ইনভয়েসের বাইরের বাকি'}: <strong>৳${num(max)}</strong></p>
      <div class="form-group"><label for="pay-amount">${invoice?'কত টাকা পেয়েছেন?':'হাতে কত নগদ পেয়েছেন?'}</label><input id="pay-amount" type="text" inputmode="decimal" autocomplete="off" value="${max.toFixed(2)}" oninput="updatePayPreview(${max})" style="width:100%"></div>
      <div class="form-group" style="margin-top:14px"><label for="pay-discount">ছাড় (ঐচ্ছিক)</label><input id="pay-discount" type="text" inputmode="decimal" value="0.00" oninput="updatePayPreview(${max})" style="width:100%"><button type="button" id="due-discount-btn" class="btn btn-ghost btn-sm" onclick="closeWithDiscount(${max})">অবশিষ্টটা ছাড় দিয়ে বিল শেষ করুন</button></div>
      <div class="form-group" ${invoice?'':'hidden'} style="margin-top:14px"><label for="pay-method">কীভাবে পেয়েছেন?</label><select id="pay-method" style="width:100%"><option value="cash">নগদ টাকা</option><option value="bkash">বিকাশ</option><option value="nagad">নগদ অ্যাপ</option><option value="bank">ব্যাংক</option></select></div>
      <div style="font-size:17px;font-weight:600;margin-top:18px;padding:12px;border-radius:8px;background:var(--surface2)">জমার পর বাকি: <span id="pay-remaining"></span></div>
      <p id="due-payment-error" role="alert" hidden style="color:var(--danger);font-size:16px;margin-top:14px"></p>
      <div style="display:flex;justify-content:flex-end;gap:12px;margin-top:22px"><button type="button" class="btn btn-ghost" id="cancel-due-btn" onclick="dismissDuePayment()">বাতিল</button><button type="button" class="btn btn-success" id="confirm-pay-btn" onclick="submitDuePayment()">পেমেন্ট কনফার্ম</button></div>
    </div>
  </div>`);
  const modal=document.getElementById('payment-modal');wireModalDismiss(modal,dismissDuePayment);
  if(attempt.payload){
    document.getElementById('pay-amount').value=attempt.payload.amount;document.getElementById('pay-discount').value=attempt.payload.discount;document.getElementById('pay-method').value=attempt.payload.method;
    dueError('আগের পেমেন্টের ফল নিশ্চিত হয়নি। যাচাই বোতাম চাপুন—নতুন করে টাকা জমা হবে না।');
  }
  updatePayPreview(max);updateDueControls();document.getElementById('confirm-pay-btn').focus();
}
function updateDueControls(){
  const locked=duePaymentBusy||!!dueAttempt?.payload;
  for(const id of ['pay-amount','pay-discount','pay-method','due-discount-btn','cancel-due-btn'])document.getElementById(id).disabled=locked;
  const btn=document.getElementById('confirm-pay-btn');btn.disabled=duePaymentBusy;
  btn.textContent=duePaymentBusy?'জমা যাচাই হচ্ছে…':dueAttempt?.payload?'আগের জমা যাচাই করুন':'পেমেন্ট কনফার্ম';
}
async function postDuePayment(attempt){
  const endpoint=attempt.kind==='sale'?`/sales/${attempt.id}/add_payment/`:`/sales/customers/${attempt.id}/collect_other_due/`;
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);
  try{
    const response=await fetch(API+endpoint,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':getCookie('csrftoken')},body:JSON.stringify(attempt.payload),signal:controller.signal});
    const data=await response.json().catch(()=>null);
    if(!response.ok||!data?.id){
      const error=new Error(data?Object.values(data).flat().join(' · '):'সার্ভারের উত্তর পড়া যায়নি।');
      error.uncertain=response.ok||response.status>=500||[408,409,425,429].includes(response.status);throw error;
    }
    return data;
  }catch(error){if(error.uncertain===undefined)error.uncertain=true;throw error;}
  finally{clearTimeout(timer);}
}
async function submitDuePayment(){
  if(duePaymentBusy||!dueAttempt)return;
  document.getElementById('due-payment-error').hidden=true;
  if(!dueAttempt.payload){
    const amount=dueCents(document.getElementById('pay-amount').value),discount=dueCents(document.getElementById('pay-discount').value);
    if(amount===null||discount===null||amount+discount<=0||amount+discount>Math.round(dueAttempt.maxAmount*100)){
      dueError('সঠিক টাকার অঙ্ক লিখুন। টাকা ও ছাড় মিলে বর্তমান বাকির বেশি হবে না।');return;
    }
    const payload={request_id:crypto.randomUUID(),expected_due:dueDecimal(Math.round(dueAttempt.maxAmount*100)),amount:dueDecimal(amount),discount:dueDecimal(discount),method:document.getElementById('pay-method').value};
    try{localStorage.setItem(DUE_PENDING_KEY,JSON.stringify({...dueAttempt,payload}));}
    catch(_){dueError('পেমেন্টের খসড়া রাখা যাচ্ছে না। ব্রাউজারের স্টোরেজ চালু করে চেষ্টা করুন।');return;}
    dueAttempt.payload=payload;
  }
  duePaymentBusy=true;updateDueControls();
  try{
    await postDuePayment(dueAttempt);
  }catch(error){
    duePaymentBusy=false;
    if(!error.uncertain){dueAttempt.payload=null;try{localStorage.removeItem(DUE_PENDING_KEY);}catch(_){}initDue();}
    updateDueControls();dueError(error.uncertain?'জমা হয়েছে কি না নিশ্চিত হয়নি। আগের জমা যাচাই করুন বোতাম চাপুন। একই পেমেন্ট দুইবার জমা হবে না।':error.message);return;
  }
  duePaymentBusy=false;dueAttempt=null;try{localStorage.removeItem(DUE_PENDING_KEY);}catch(_){}
  document.getElementById('payment-modal')?.remove();toast('পেমেন্ট জমা হয়েছে। টাকা ও বাকি আপডেট হয়েছে।');initDue();
}
function resumePendingDue(){
  try{
    const saved=JSON.parse(localStorage.getItem(DUE_PENDING_KEY)||'null');
    if(saved?.payload?.request_id&&['sale','customer'].includes(saved.kind)&&Number.isInteger(saved.id)&&Number.isFinite(saved.maxAmount)){
      dueAttempt=saved;openDuePayment(saved.kind,saved.id,saved.maxAmount);
    }
  }catch(_){toast('আগের পেমেন্টের খসড়া পড়া যায়নি। নতুন জমার আগে হিসাব যাচাই করুন।','error');}
}
window.addEventListener('beforeunload',event=>{if(dueAttempt?.payload){event.preventDefault();event.returnValue='';}});
document.addEventListener('DOMContentLoaded',()=>{initDue();resumePendingDue();});
