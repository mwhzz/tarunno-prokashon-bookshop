const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/pos-workspace.js'), 'utf8');

function harness() {
  const nodes = new Map(), storage = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {id, value:'', textContent:'', innerHTML:'', hidden:false,
      style:{},attributes:{},classList:{add(){},remove(){},toggle(){}},focus(){},select(){},setAttribute(k,v){this.attributes[k]=v;},scrollIntoView(){},checkValidity(){return true;}});
    return nodes.get(id);
  };
  const context = vm.createContext({Intl, Number, Math, Object, Array, String, JSON, Date, Set,
    encodeURIComponent, crypto:require('node:crypto').webcrypto, setTimeout:()=>0,clearTimeout(){},
    window:{addEventListener(){}},localStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k),removeItem:k=>storage.delete(k)},
    document:{getElementById:node,addEventListener(){},querySelector:node,querySelectorAll:()=>[...nodes.values()]},
    icon:()=>'',toast(){},confirmModal:async()=>true,api:async()=>({results:[],next:null})});
  vm.runInContext(source,context);
  for(const id of ['commission','discount','pack','courier','txfee','paid'])node(id).value='0';
  node('sale-type').value='retail';node('pay-method').value='cash';node('filter-sort').value='name';
  const run = code => vm.runInContext(code,context);
  run('catalogLoading=false;draftReady=true;');
  const book = {id:1,title:'Test <Book>',author:'Author',isbn:'9781234567890',is_active:true,mrp:'200.00',selling_price:'160.00',commission:'20.00',shop_quantity:3};
  context.sample=book;run('allBooks=[sample];');
  return {node,context,run,storage};
}

test('ISBN search, stock limits, retail and wholesale discounts, fractional totals',()=>{
  const h=harness();h.node('book-search').value='9781234567890';
  assert.equal(h.run('getFilteredBooks().length'),1);
  h.run('addToCart(1);changeQty(1,1);changeQty(1,1);changeQty(1,1)');
  assert.equal(h.run('cart[0].qty'),3);
  h.node('commission').value='5';h.node('txfee').value='0.50';
  assert.equal(h.run('currentBillTotal()'),450.5); // 600 less 25% plus fee.
  h.node('sale-type').value='wholesale';assert.equal(h.run('currentBillTotal()'),570.5);
  h.run('renderCart()');assert.ok(h.node('cart-scroll').innerHTML.includes('Test &lt;Book&gt;'));
});

test('restores full draft after catalog load without overwriting it during initial render',async()=>{
  const h=harness();h.storage.set('pos_cart_draft_v1',JSON.stringify({cart:[{bid:1,qty:2,extraCommission:5}],fields:{'sale-type':'retail',commission:'7',discount:'28',pack:'5',courier:'0',txfee:'0','cus-name':'Buyer','cus-phone':'01700000000','pay-method':'cash',paid:'100'}}));
  h.context.api=async path=>path.includes('/books/')?{results:[h.context.sample],next:null}:{results:[],next:null};
  await h.run('initPOS()');
  assert.equal(h.run('cart.length'),1);assert.equal(h.run('cart[0].qty'),2);
  assert.equal(h.node('commission').value,'7');assert.equal(h.node('cus-name').value,'Buyer');
  assert.equal(h.node('paid').value,'100');assert.equal(h.node('pack').value,'5');
});

test('blocks unnamed due sales and negative charges before creating an invoice',async()=>{
  const h=harness();h.run("addToCart(1);chooseSettlement('due')");let posts=0;h.context.sendCheckout=async()=>{posts++;};
  await h.run('completeSale()');assert.equal(posts,0);assert.ok(h.node('checkout-error').textContent);
  h.run("chooseSettlement('full')");h.node('paid').value='200';h.node('pack').value='-1';await h.run('completeSale()');assert.equal(posts,0);
});

test('one POST for repeated clicks, records only bill amount, refresh failure keeps sale complete',async()=>{
  const h=harness();h.run("addToCart(1);chooseSettlement('full')");h.node('paid').value='500';let posts=0,payload,release;
  h.context.sendCheckout=async body=>{posts++;payload=body;await new Promise(r=>release=r);return{id:12,total:'160.00'};};h.context.api=async()=>{throw Error('refresh offline');};
  h.run('showSuccessModal = () => {};');
  const first=h.run('completeSale()');await h.run('completeSale()');assert.equal(posts,1);
  release();await first;
  assert.equal(payload.paid_amount,'160.00');assert.equal(payload.items[0].discount,'40.00');
  assert.equal(h.run('cart.length'),0);assert.equal(h.run('allBooks[0].shop_quantity'),2);
  await h.run('completeSale()');assert.equal(posts,1);
});

test('failed sale retains cart and payment so cashier can correct the issue',async()=>{
  const h=harness();h.run("addToCart(1);chooseSettlement('full')");h.node('paid').value='160';
  h.context.sendCheckout=async()=>{throw {detail:'Stock changed',uncertain:false};};await h.run('completeSale()');
  assert.equal(h.run('cart.length'),1);assert.equal(h.node('paid').value,'160');
  assert.equal(h.run('saleSubmitting'),false);assert.equal(h.node('checkout-error').textContent,'Stock changed');
});

test('whole payment fills the amount automatically and cash change is separate from recorded payment',()=>{
  const h=harness();h.run("addToCart(1);goToPayment();chooseSettlement('full')");
  assert.equal(h.node('paid').value,'160.00');assert.equal(h.node('complete-btn').disabled,false);
  h.run('setPaidAmount(500)');assert.equal(h.node('outcome-label').textContent,'ক্রেতাকে ফেরত দিন');
  assert.equal(h.node('outcome-amount').textContent,'৳৩৪০');assert.equal(h.node('recorded-paid').textContent,'৳১৬০');
  h.run("choosePaymentMethod('bank')");assert.equal(h.node('complete-btn').disabled,true);
  h.run('setPaidFull()');assert.equal(h.node('complete-btn').disabled,false);
});

test('partial and due choices require customer identity and keep old due outside this bill',async()=>{
  const h=harness();h.run("addToCart(1);chooseSettlement('partial')");
  h.node('paid').value='৫০';h.run('onPaidInput()');assert.equal(h.node('t-due').textContent,'৳১১০');
  assert.equal(h.node('customer-area').hidden,false);let payload;
  h.context.sendCheckout=async body=>{payload=body;return{id:1,total:'160.00',due_amount:'110.00'};};h.run('showSuccessModal=()=>{};');
  await h.run('completeSale()');assert.equal(payload,undefined);
  h.node('cus-name').value='Buyer';h.node('cus-phone').value='০১৭০০০০০০০০';
  h.run('currentCustomerDue=300;renderPaymentState()');assert.equal(h.node('t-total').textContent,'৳১৬০');
  assert.equal(h.node('t-grand-due').textContent,'৳৪১০');await h.run('completeSale()');
  assert.equal(payload.paid_amount,'50.00');assert.equal(payload.customer_phone,'01700000000');
});

test('invalid amounts and incorrect settlement choice cannot complete a sale',async()=>{
  const h=harness();h.run('addToCart(1)');let posts=0;h.context.sendCheckout=async()=>{posts++;};
  await h.run('completeSale()');assert.equal(posts,0);
  h.run("chooseSettlement('full')");h.node('paid').value='150';h.run('onPaidInput()');
  assert.equal(h.node('complete-btn').disabled,true);await h.run('completeSale()');assert.equal(posts,0);
  h.run("chooseSettlement('partial')");h.node('paid').value='160';h.run('onPaidInput()');
  assert.equal(h.node('complete-btn').disabled,true);await h.run('completeSale()');assert.equal(posts,0);
  h.node('paid').value='1e3';h.run('onPaidInput()');assert.equal(h.node('complete-btn').disabled,true);
});

test('Bengali money entry and cents arithmetic avoid floating point payment errors',()=>{
  const h=harness();assert.equal(h.run("inputCents('১,২৩,৪৫৬.৭৮')"),12345678);
  assert.equal(h.run("inputCents('1,000.50')"),100050);
  for(const raw of ['1,00','-1','Infinity','1e3','1.999'])assert.equal(h.run(`inputCents(${JSON.stringify(raw)})`),null);
  h.run('sample.mrp="0.10";sample.commission="0";addToCart(1);setQty(1,3)');
  h.node('pack').value='0.10';h.node('courier').value='0.20';
  assert.equal(h.run('billCents()'),60);assert.equal(h.run('currentBillTotal()'),0.6);
});

test('changing the reviewed bill requires the cashier to choose payment again',()=>{
  const h=harness();h.run("addToCart(1);goToPayment();chooseSettlement('full')");
  h.node('pack').value='10';h.run('calcTotals()');assert.equal(h.run('settlementMode'),null);
  assert.equal(h.node('complete-btn').disabled,true);assert.equal(h.node('t-total').textContent,'৳১৭০');
});

test('uncertain network result freezes the bill and retries the identical request ID and payload',async()=>{
  const h=harness();h.run("addToCart(1);chooseSettlement('full')");const attempts=[];
  h.context.sendCheckout=async body=>{attempts.push(JSON.stringify(body));if(attempts.length===1)throw Object.assign(new Error('network lost'),{uncertain:true});return{id:1,total:'160.00'};};
  h.run('showSuccessModal=()=>{}');await h.run('completeSale()');
  assert.ok(h.run('pendingSalePayload'));assert.equal(h.node('complete-btn').disabled,false);
  h.run("changeQty(1,1);chooseSettlement('due')");assert.equal(h.run('cart[0].qty'),1);assert.equal(h.run('settlementMode'),'full');
  await h.run('completeSale()');assert.equal(attempts.length,2);assert.equal(attempts[0],attempts[1]);
  assert.equal(h.run('cart.length'),0);assert.equal(h.run('pendingSalePayload'),null);
});

test('reload can resolve an uncertain sale even if that sale used the last stock',async()=>{
  const h=harness();h.run("addToCart(1);chooseSettlement('full')");
  h.context.sendCheckout=async()=>{throw Object.assign(new Error('network lost'),{uncertain:true});};await h.run('completeSale()');
  const frozen=h.run('JSON.stringify(pendingSalePayload)');
  h.context.api=async()=>({results:[],next:null});await h.run('initPOS()');
  assert.equal(h.run('posStep'),'payment');assert.equal(h.run('cart.length'),1);
  assert.equal(h.node('cart-stage').hidden,false);assert.equal(h.node('complete-btn').disabled,false);
  let retried;h.context.sendCheckout=async body=>{retried=JSON.stringify(body);return{id:1,total:'160.00'};};
  h.run('showSuccessModal=()=>{}');await h.run('completeSale()');assert.equal(retried,frozen);
});

test('quantity entry accepts Bengali whole numbers, rejects empty or fractional quantities and preserves the row',()=>{
  const h=harness();h.run('addToCart(1);setQty(1,"২")');assert.equal(h.run('cart[0].qty'),2);
  assert.equal(h.node('line-total-1').textContent,'৳৩২০');
  for(const bad of ['', '0', '-1', '2.5']) {h.context.quantity=bad;h.run('setQty(1,quantity)');assert.equal(h.run('cart[0].qty'),2);}
  h.run('setQty(1,100)');assert.equal(h.run('cart[0].qty'),3);
});

test('fresh stock errors return to the ledger and block checkout until the row is fixed',async()=>{
  const h=harness();h.run("addToCart(1);goToPayment();chooseSettlement('full')");
  h.context.sendCheckout=async()=>{throw Object.assign(new Error('Stock changed'),{uncertain:false});};
  h.context.api=async()=>({results:[],next:null});await h.run('completeSale()');
  assert.equal(h.run('posStep'),'books');assert.equal(h.run('cart[0].max'),0);
  assert.equal(h.node('checkout-btn').disabled,true);assert.ok(h.node('cart-scroll').innerHTML.includes('stock-error'));
  h.run('setQty(1,1)');assert.equal(h.run('cart[0].qty'),1);
});


test('ledger and payment remain visible together with adjustments and customer fields open',()=>{
  const h=harness();h.run('renderPOS(document.getElementById("pos-root"));addToCart(1);goToPayment();');
  const markup=h.node('pos-root').innerHTML;
  assert.ok(markup.includes('class="sale-options-card"'));
  assert.ok(!markup.includes('<details class="order-options"'));
  assert.ok(!markup.includes('<section id="payment-stage" hidden'));
  assert.equal(h.node('cart-stage').hidden,false);
  assert.equal(h.node('payment-entry').hidden,false);
  assert.equal(h.node('customer-area').hidden,false);
  assert.equal(h.node('received-field').hidden,false);
});
