from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum, Count
from django.utils import timezone
from datetime import timedelta
from .models import Sale, SaleItem, Customer, ExternalTrade
from .serializers import (
    SaleSerializer, SaleCreateSerializer, CustomerSerializer,
    ExternalTradeSerializer, ExternalTradeCreateSerializer,
)
from django.conf import settings
from django.db import transaction
from uuid import UUID
import hashlib
import json
from .models import SaleSubmission
from django.http import HttpResponse
from django.template.loader import render_to_string
import os

BENGALI_DIGITS = str.maketrans('0123456789', '০১২৩৪৫৬৭৮৯')
BENGALI_MONTHS = [
    'জানুয়ারি', 'ফেব্রুয়ারি', 'মার্চ', 'এপ্রিল', 'মে', 'জুন',
    'জুলাই', 'আগস্ট', 'সেপ্টেম্বর', 'অক্টোবর', 'নভেম্বর', 'ডিসেম্বর',
]


def bengali_date_str(dt):
    """সার্ভার-সাইড PDF জেনারেশনে (invoice_pdf.html) JS-এর মতো Intl locale ফরম্যাটিং পাওয়া
    যায় না, তাই এখানে ম্যানুয়ালি ইংরেজি মাসের নাম/সংখ্যা বাংলায় রূপান্তর করা হচ্ছে —
    নাহলে PDF-এ "০৩ August, ২০২৬"-এর মতো মিশ্র বাংলা-ইংরেজি তারিখ দেখাতো।"""
    day = str(dt.day).translate(BENGALI_DIGITS)
    month = BENGALI_MONTHS[dt.month - 1]
    year = str(dt.year).translate(BENGALI_DIGITS)
    return f"{day} {month}, {year}"



class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.prefetch_related('items__book').order_by('-created_at')
    filter_backends = [filters.SearchFilter]
    search_fields = ['invoice_number', 'customer_name', 'customer_phone']
    http_method_names = ['get', 'post', 'head', 'options']  # no edit/delete from POS

    def get_serializer_class(self):
        if self.action == 'create':
            return SaleCreateSerializer
        return SaleSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        submission = None
        request_id = request.data.get('request_id')
        if request_id:
            try:
                request_id = UUID(str(request_id))
            except (ValueError, TypeError, AttributeError):
                return Response({'detail': 'Invalid checkout request ID.'}, status=400)
            payload = {key: value for key, value in request.data.items() if key != 'request_id'}
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            submission, _ = SaleSubmission.objects.get_or_create(
                request_id=request_id, defaults={'payload_hash': digest},
            )
            submission = SaleSubmission.objects.select_for_update().get(pk=request_id)
            if submission.payload_hash != digest:
                return Response({'detail': 'এই বিলের তথ্য বদলে গেছে। আগের সেভের ফল আগে যাচাই করুন।'}, status=409)
            if submission.sale_id:
                return Response(SaleSerializer(submission.sale).data, status=200)
        serializer = SaleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = serializer.save()
        if submission:
            submission.sale = sale
            submission.save(update_fields=['sale'])
        return Response(
            SaleSerializer(sale).data,
            status=status.HTTP_201_CREATED
        )

    def get_queryset(self):
        qs = super().get_queryset()
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        return qs

    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        """ড্যাশবোর্ডের জন্য বিস্তারিত আর্থিক ও স্টক ডাটা"""
        from .reports import sales_report, stock_value_report, get_date_range
        
        period = request.query_params.get('period', 'month')
        date_from, date_to = get_date_range(period)
        
        # ১. আর্থিক সামারি (বিক্রয়, লাভ, বকেয়া, খরচ)
        stats = sales_report(date_from, date_to)
        
        # ২. স্টক সামারি
        stock = stock_value_report()
        
        # ৩. আজকের কুইক স্ট্যাটস (সবসময় দেখানোর জন্য)
        today = timezone.now().date()
        today_sales = Sale.objects.filter(created_at__date=today)
        today_external_profit = sum(float(t.profit) for t in ExternalTrade.objects.filter(status='sold', sold_at__date=today))
        today_stats = {
            'total_revenue': float(today_sales.aggregate(total=Sum('total'))['total'] or 0),
            'total_paid': float(today_sales.aggregate(paid=Sum('paid_amount'))['paid'] or 0),
            'total_due': float(today_sales.aggregate(due=Sum('due_amount'))['due'] or 0),
            'total_invoices': today_sales.count(),
            'external_trade_profit': today_external_profit,
            'realized_profit': sum(float(s.realized_profit) for s in today_sales) + today_external_profit,
        }

        return Response({
            'today': today_stats,
            'this_month': stats['summary'],
            'stats': stats,
            'stock': stock,
            'due_invoices': Sale.objects.filter(status__in=['due', 'partial']).count()
        })

    @action(detail=False, methods=['get'])
    def due_list(self, request):
        """বাকি বিলের তালিকা"""
        dues = Sale.objects.filter(status__in=['due', 'partial'])
        invoices = SaleSerializer(dues, many=True).data

        # কিছু গ্রাহকের বাকি কোনো নির্দিষ্ট ইনভয়েসের সাথে যুক্ত নয় (যেমন: গ্রাহক যোগ করার
        # সময় সরাসরি বসানো ওপেনিং ব্যালেন্স) — সেগুলো এখানে আলাদাভাবে দেখানো হচ্ছে,
        # নাহলে টাকা বাকি থাকলেও এই তালিকায় কখনো দেখা যেত না।
        customer_dues = []
        for customer in Customer.objects.filter(total_due__gt=0):
            sales_due = Sale.objects.filter(
                customer=customer, status__in=['due', 'partial']
            ).aggregate(total=Sum('due_amount'))['total'] or 0
            extra = customer.total_due - sales_due
            if extra > 0:
                customer_dues.append({
                    'id': customer.id,
                    'name': customer.name,
                    'phone': customer.phone,
                    'customer_code': customer.customer_code,
                    'amount': extra,
                })

        return Response({'invoices': invoices, 'customer_dues': customer_dues})

    @action(detail=True, methods=['post'])
    def add_payment(self, request, pk=None):
        from .due_payments import collect_invoice_due
        return Response(collect_invoice_due(self.get_object().pk, request.data))

    @action(detail=True, methods=['post'])
    def edit(self, request, pk=None):
        """ভুল মেমো ঠিক করা; আগের অবস্থা ইতিহাসে থেকে যায়"""
        from .sale_edits import edit_sale
        sale = edit_sale(self.get_object().pk, request.data, request.user)
        return Response(SaleSerializer(sale).data)

    @action(detail=True, methods=['get'])
    def edit_history(self, request, pk=None):
        sale = self.get_object()
        edits = sale.edits.select_related('edited_by')
        return Response({
            'edit_count': edits.count(),
            'has_returns': sale.returns.exists(),
            'edits': [
                {'id': e.id, 'created_at': e.created_at, 'reason': e.reason, 'changes': e.changes,
                 'edited_by': e.edited_by.get_full_name() or e.edited_by.get_username() if e.edited_by else ''}
                for e in edits
            ],
        })

    @action(detail=True, methods=['post'])
    def remove_payment(self, request, pk=None):
        """ভুলবশত (যেমন ডাবল-ক্লিকে) দুইবার পেমেন্ট বসে গেলে একটা মুছে ইনভয়েস/কাস্টমারের
        হিসাব আগের অবস্থায় ফিরিয়ে আনার জন্য।"""
        from django.db import transaction
        from accounts.models import CashTransaction, DailyCash
        from .models import Payment

        sale = self.get_object()
        payment_id = request.data.get('payment_id')
        try:
            payment = sale.payments.get(id=payment_id)
        except Payment.DoesNotExist:
            return Response({'error': 'এই পেমেন্ট পাওয়া যায়নি।'}, status=status.HTTP_404_NOT_FOUND)
        if payment.amount < 0:
            return Response({'error': 'বই ফেরতের টাকা ফেরত মোছা যাবে না।'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            sale.discount -= payment.discount
            sale.total = sale.subtotal - sale.discount + sale.packaging_charge + sale.courier_charge + sale.transaction_fee
            sale.paid_amount -= payment.amount
            sale.save()

            if sale.customer:
                sale.customer.total_due += (payment.amount + payment.discount)
                sale.customer.save()

            reference = f"sale_payment_{payment.id}"
            cash_txns = CashTransaction.objects.filter(reference_id__in=[reference, f"pay_{payment.id}"])
            daily_cash_ids = list(cash_txns.values_list('daily_cash_id', flat=True).distinct())
            cash_txns.delete()

            payment.delete()

            for dc_id in daily_cash_ids:
                DailyCash.objects.get(id=dc_id).update_closing_balance()

        return Response(SaleSerializer(sale).data)

    @action(detail=True, methods=['post'])
    def return_items(self, request, pk=None):
        """বই ফেরত — স্টক দোকানে ফেরে, বিল কমে, দরকার হলে টাকা ফেরত যায়"""
        from .sale_returns import return_sale_items
        result = return_sale_items(self.get_object().pk, request.data)
        return Response({**result, 'sale': SaleSerializer(Sale.objects.get(pk=pk)).data})

    @action(detail=True, methods=['get'])
    def download_pdf(self, request, pk=None):
        """ইনভয়েসের PDF ডাউনলোড"""
        from xhtml2pdf import pisa
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        
        sale = self.get_object()
        
        # Path configuration for PDF engine
        font_path = os.path.join(settings.BASE_DIR, 'static', 'fonts', 'kalpurush.ttf')
        logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo.png')
        
        # Register font with reportlab
        try:
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.pdfbase import pdfmetrics
            pdfmetrics.registerFont(TTFont('Kalpurush', font_path))
        except Exception as e:
            pass
            
        # Convert path to URI for CSS @font-face on Windows
        import pathlib
        font_uri = pathlib.Path(font_path).as_uri()
        
        context = {
            'sale': sale,
            'logo_path': logo_path,
            'font_path': font_uri,
            'invoice_date_bn': bengali_date_str(sale.created_at),
        }
        
        # Render HTML to string
        html = render_to_string('invoice_pdf.html', context)
        
        # Create PDF response
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="Invoice_{sale.invoice_number}.pdf"'
        
        # Generate PDF
        pisa_status = pisa.CreatePDF(html, dest=response)
        
        if pisa_status.err:
            return HttpResponse('Error generating PDF', status=500)
            
        return response

    @action(detail=True, methods=['get'])
    def print_invoice(self, request, pk=None):
        """Printable browser invoice with Bangla font, theme, and logo."""
        sale = self.get_object()
        html = render_to_string('invoice_print.html', {'sale': sale}, request=request)
        return HttpResponse(html)


class ExternalTradeViewSet(viewsets.ModelViewSet):
    """বাইরে থেকে কেনা ও সরাসরি বিক্রি করা বই (দোকানের স্টকে ঢোকে না)"""
    queryset = ExternalTrade.objects.all()
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_serializer_class(self):
        if self.action == 'create':
            return ExternalTradeCreateSerializer
        return ExternalTradeSerializer

    def partial_update(self, request, *args, **kwargs):
        """
        ট্রেড এডিট করলে সংশ্লিষ্ট cash_out/cash_in লেজার এন্ট্রিও নতুন
        দাম/পরিমাণ অনুযায়ী সিঙ্ক করা হয়, যাতে ক্যাশ হিসাব ভুল না থাকে।
        """
        from accounts.models import CashTransaction, DailyCash

        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        trade = serializer.save()

        reference = f"ext_{trade.id}"
        CashTransaction.objects.filter(reference_id=reference, transaction_type='cash_out').update(
            amount=trade.total_purchase,
            note=f"বাইরের বই ক্রয়: {trade.book_title} x{trade.quantity}",
        )
        if trade.status == 'sold':
            CashTransaction.objects.filter(reference_id=reference, transaction_type='cash_in').update(
                amount=trade.total_sale,
                note=f"বাইরের বই বিক্রয়: {trade.book_title} x{trade.quantity}",
            )

        daily_cash_ids = CashTransaction.objects.filter(reference_id__in=[reference, f"pay_{payment.id}"]).values_list('daily_cash_id', flat=True).distinct()
        for dc_id in daily_cash_ids:
            DailyCash.objects.get(id=dc_id).update_closing_balance()

        return Response(ExternalTradeSerializer(trade).data)

    def destroy(self, request, *args, **kwargs):
        """ট্রেড মুছে ফেললে সংশ্লিষ্ট ক্যাশ লেজার এন্ট্রিও মুছে হিসাব রিক্যালকুলেট হয়।"""
        from accounts.models import CashTransaction, DailyCash

        instance = self.get_object()
        reference = f"ext_{instance.id}"
        daily_cash_ids = list(CashTransaction.objects.filter(reference_id__in=[reference, f"pay_{payment.id}"]).values_list('daily_cash_id', flat=True).distinct())
        CashTransaction.objects.filter(reference_id__in=[reference, f"pay_{payment.id}"]).delete()
        instance.delete()
        for dc_id in daily_cash_ids:
            DailyCash.objects.get(id=dc_id).update_closing_balance()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get_queryset(self):
        qs = super().get_queryset()
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        publisher = self.request.query_params.get('publisher')
        if publisher:
            qs = qs.filter(publisher=publisher)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trade = serializer.save()
        return Response(ExternalTradeSerializer(trade).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def complete_sale(self, request, pk=None):
        """পেন্ডিং থাকা ট্রেডের বিক্রয়মূল্য বসিয়ে বিক্রি সম্পন্ন করা"""
        from decimal import Decimal, InvalidOperation
        from accounts.models import DailyCash, CashTransaction

        trade = self.get_object()
        if trade.status == 'sold':
            return Response({'error': 'এই ট্রেডের বিক্রয় ইতিমধ্যে সম্পন্ন হয়েছে।'}, status=status.HTTP_400_BAD_REQUEST)

        sale_commission_raw = request.data.get('sale_commission')
        try:
            if sale_commission_raw not in (None, ''):
                sale_commission = Decimal(str(sale_commission_raw))
                if trade.body_rate <= 0:
                    return Response({'error': 'কমিশন % দিয়ে হিসাব করতে হলে আগে বডি রেট দিতে হবে।'}, status=status.HTTP_400_BAD_REQUEST)
                selling_price = trade.body_rate * (1 - sale_commission / Decimal('100'))
                trade.sale_commission = sale_commission
            else:
                selling_price = Decimal(str(request.data.get('selling_price')))
        except (TypeError, InvalidOperation):
            return Response({'error': 'সঠিক বিক্রয়মূল্য বা কমিশন % দিন।'}, status=status.HTTP_400_BAD_REQUEST)
        if selling_price < 0:
            return Response({'error': 'সঠিক বিক্রয়মূল্য দিন।'}, status=status.HTTP_400_BAD_REQUEST)

        customer_name = request.data.get('customer_name')
        customer_phone = request.data.get('customer_phone')
        if customer_name:
            trade.customer_name = customer_name
        if customer_phone:
            trade.customer_phone = customer_phone

        trade.selling_price = selling_price
        trade.status = 'sold'
        trade.sold_at = timezone.now()
        trade.save()

        daily_cash = DailyCash.get_for_today()
        CashTransaction.objects.create(
            daily_cash=daily_cash,
            transaction_type='cash_in',
            amount=trade.total_sale,
            note=f"বাইরের বই বিক্রয়: {trade.book_title} x{trade.quantity}",
            reference_id=f"ext_{trade.id}",
        )

        return Response(ExternalTradeSerializer(trade).data)

    @action(detail=True, methods=['post'])
    def add_to_stock(self, request, pk=None):
        """
        এই ট্রেডের বইটি স্থায়ীভাবে নিজের Book ক্যাটালগ ও স্টকে যোগ করা।
        ট্রেড রেকর্ডটি (ক্যাশ হিসাবসহ) আলাদা ইতিহাস হিসেবে থেকেই যায় —
        শুধু ভবিষ্যতে এই বইটি সাধারণ POS/স্টক দিয়েও বিক্রি করা যাবে।
        """
        from decimal import Decimal, InvalidOperation
        from books.models import Book
        from stock.models import StockSummary, StockEntry

        trade = self.get_object()
        if trade.converted_book_id:
            return Response({'error': 'এই বইটি ইতিমধ্যে স্টকে যোগ করা হয়েছে।'}, status=status.HTTP_400_BAD_REQUEST)

        location = request.data.get('location', 'shop')
        if location not in ('shop', 'godown'):
            return Response({'error': 'সঠিক লোকেশন দিন (shop/godown)।'}, status=status.HTTP_400_BAD_REQUEST)

        # ক্রয়মূল্য কখনো বিক্রয়ের দাম হিসেবে বসানো হয় না — নাহলে POS-এ কেনা দাম দেখা যেত
        try:
            selling_price = Decimal(str(request.data.get('selling_price') or trade.selling_price or 0))
        except InvalidOperation:
            return Response({'error': 'সঠিক বিক্রয়মূল্য দিন।'}, status=status.HTTP_400_BAD_REQUEST)
        if selling_price <= 0:
            return Response({'error': 'বিক্রয়মূল্য/কপি লিখুন।'}, status=status.HTTP_400_BAD_REQUEST)

        # গায়ের মূল্য (বডি রেট) থাকলে POS-এ সেটা দেখাবে, আর বিক্রয়মূল্য পর্যন্ত বাকিটা কমিশন
        if trade.body_rate > 0:
            mrp = trade.body_rate
            commission = max(Decimal('0'), min(Decimal('100'), (1 - selling_price / mrp) * 100)).quantize(Decimal('0.01'))
        else:
            mrp, commission = selling_price, Decimal('0')

        book = Book.objects.create(
            title=trade.book_title,
            author=trade.author,
            publisher=trade.publisher,
            source='external',
            purchase_price=trade.purchase_price,
            selling_price=selling_price,
            mrp=mrp,
            commission=commission,
            notes=f"বাইরের ক্রয়-বিক্রয় থেকে যোগ করা হয়েছে (External Trade #{trade.id})",
        )

        # যতগুলো কপি এখনো বিক্রি হয়নি (pending), শুধু ততগুলোই সত্যিকারের স্টকে যোগ হবে।
        add_qty = trade.quantity if trade.status == 'pending' else 0
        if add_qty > 0:
            StockEntry.objects.create(
                book=book,
                quantity=add_qty,
                source='purchase',
                location=location,
                purchase_price=trade.purchase_price,
                supplier_name=trade.note[:200] if trade.note else '',
                note=f"বাইরের ক্রয় থেকে স্টকে যোগ (External Trade #{trade.id})",
            )
            StockSummary.update_stock(book, add_qty, location=location)

        trade.converted_book = book
        trade.save(update_fields=['converted_book'])

        return Response({
            'book_id': book.id,
            'book_title': book.title,
            'stock_added': add_qty,
            'trade': ExternalTradeSerializer(trade).data,
        })

    @action(detail=False, methods=['get'])
    def summary(self, request):
        from django.db.models import F, Q, DecimalField
        from django.db.models.functions import Coalesce

        qs = self.get_queryset()
        total_purchase = qs.aggregate(
            total=Sum(F('purchase_price') * F('quantity'), output_field=DecimalField())
        )['total'] or 0
        sold = qs.filter(status='sold')
        # লাভ শুধু বিক্রি হওয়া ট্রেডের ক্রয়মূল্যের বিপরীতে হিসাব হবে —
        # পেন্ডিং (এখনো বিক্রি না হওয়া) ট্রেডের ক্রয়মূল্য এখানে বাদ দিলে
        # স্টকে পড়ে থাকা বই থাকলেই লাভ ভুলভাবে ঋণাত্মক দেখাতো।
        total_purchase_of_sold = sold.aggregate(
            total=Sum(F('purchase_price') * F('quantity'), output_field=DecimalField())
        )['total'] or 0
        total_sale = sold.aggregate(
            total=Sum(F('selling_price') * F('quantity'), output_field=DecimalField())
        )['total'] or 0
        pending = qs.filter(status='pending')
        pending_amount = pending.aggregate(
            total=Sum(F('purchase_price') * F('quantity'), output_field=DecimalField())
        )['total'] or 0

        return Response({
            'total_trades': qs.count(),
            'pending_count': pending.count(),
            'pending_amount': pending_amount,
            'total_purchase': total_purchase,
            'total_sale': total_sale,
            'total_profit': total_sale - total_purchase_of_sold,
        })

    @action(detail=False, methods=['get'])
    def by_publisher(self, request):
        """প্রকাশনী অনুযায়ী কেনা বই, কমিশন ও লাভের সারাংশ"""
        from django.db.models import F, DecimalField, Avg, Case, When

        qs = self.get_queryset().exclude(publisher='')

        rows = qs.values('publisher').annotate(
            trade_count=Count('id'),
            total_qty=Sum('quantity'),
            total_purchase=Sum(F('purchase_price') * F('quantity'), output_field=DecimalField()),
            total_sale=Sum(
                Case(
                    When(status='sold', then=F('selling_price') * F('quantity')),
                    default=0,
                    output_field=DecimalField(),
                )
            ),
            avg_purchase_commission=Avg('purchase_commission'),
            avg_sale_commission=Avg(Case(When(status='sold', then=F('sale_commission')))),
        ).order_by('publisher')

        results = []
        for row in rows:
            total_purchase = row['total_purchase'] or 0
            total_sale = row['total_sale'] or 0
            results.append({
                'publisher': row['publisher'],
                'trade_count': row['trade_count'],
                'total_qty': row['total_qty'] or 0,
                'total_purchase': total_purchase,
                'total_sale': total_sale,
                'total_profit': total_sale - total_purchase,
                'avg_purchase_commission': row['avg_purchase_commission'] or 0,
                'avg_sale_commission': row['avg_sale_commission'] or 0,
            })

        return Response({'publishers': results})


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'phone', 'customer_code']

    def get_queryset(self):
        qs = super().get_queryset()
        c_type = self.request.query_params.get('customer_type')
        if c_type:
            qs = qs.filter(customer_type=c_type)
        return qs

    @action(detail=False, methods=['get'])
    def find_by_phone(self, request):
        phone = request.query_params.get('phone')
        if not phone:
            return Response({'error': 'Phone number required'}, status=status.HTTP_400_BAD_REQUEST)

        customer = Customer.objects.filter(phone=phone).first()
        if customer:
            return Response(CustomerSerializer(customer).data)
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'])
    def collect_other_due(self, request, pk=None):
        from .due_payments import collect_customer_due
        return Response(collect_customer_due(self.get_object().pk, request.data))


from rest_framework.decorators import api_view
from rest_framework.response import Response
from .reports import sales_report, stock_value_report, get_date_range
import datetime


@api_view(['GET'])
def report_view(request):
    period = request.query_params.get('period', 'month')
    date_from_str = request.query_params.get('date_from')
    date_to_str = request.query_params.get('date_to')

    try:
        df = datetime.date.fromisoformat(date_from_str) if date_from_str else None
        dt = datetime.date.fromisoformat(date_to_str) if date_to_str else None
    except ValueError:
        return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)

    date_from, date_to = get_date_range(period, df, dt)
    data = sales_report(date_from, date_to)
    data['stock'] = stock_value_report()
    return Response(data)
