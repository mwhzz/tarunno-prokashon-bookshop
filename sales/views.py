from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum, Count
from django.utils import timezone
from datetime import timedelta
from .models import Sale, SaleItem, Customer
from .serializers import SaleSerializer, SaleCreateSerializer, CustomerSerializer
from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
import os



class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.prefetch_related('items__book').order_by('-created_at')
    filter_backends = [filters.SearchFilter]
    search_fields = ['invoice_number', 'customer_name', 'customer_phone']
    http_method_names = ['get', 'post', 'head', 'options']  # no edit/delete from POS

    def get_serializer_class(self):
        if self.action == 'create':
            return SaleCreateSerializer
        return SaleSerializer

    def create(self, request, *args, **kwargs):
        serializer = SaleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = serializer.save()
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
        today_stats = {
            'revenue': today_sales.aggregate(total=Sum('total'))['total'] or 0,
            'paid': today_sales.aggregate(paid=Sum('paid_amount'))['paid'] or 0,
            'invoices': today_sales.count()
        }

        return Response({
            'today': today_stats,
            'stats': stats,
            'stock': stock,
            'due_invoices_count': Sale.objects.filter(status__in=['due', 'partial']).count()
        })

    @action(detail=False, methods=['get'])
    def due_list(self, request):
        """বাকি বিলের তালিকা"""
        dues = Sale.objects.filter(status__in=['due', 'partial'])
        serializer = SaleSerializer(dues, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def add_payment(self, request, pk=None):
        """ইনভয়েসের বাকি টাকা জমা নেওয়া"""
        from decimal import Decimal
        sale = self.get_object()
        amount_str = request.data.get('amount')
        method = request.data.get('method', 'cash')
        
        if not amount_str:
            return Response({'error': 'Amount is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            amount = Decimal(str(amount_str))
        except:
            return Response({'error': 'Invalid amount format'}, status=status.HTTP_400_BAD_REQUEST)
            
        if amount > sale.due_amount:
            return Response({'error': 'Amount exceeds due balance'}, status=status.HTTP_400_BAD_REQUEST)
            
        from django.db import transaction
        from .models import Payment
        
        with transaction.atomic():
            # ১. পেমেন্ট রেকর্ড তৈরি
            Payment.objects.create(
                sale=sale,
                amount=amount,
                method=method
            )
            
            # ২. ইনভয়েস আপডেট
            sale.paid_amount += amount
            # Sale.save() অটোমেটিক due_amount এবং status আপডেট করবে
            sale.save()
            
            # ৩. কাস্টমার ব্যালেন্স আপডেট
            if sale.customer:
                sale.customer.total_due -= amount
                sale.customer.save()
                
        return Response(SaleSerializer(sale).data)

    @action(detail=True, methods=['post'])
    def return_items(self, request, pk=None):
        """বই ফেরত নেওয়া"""
        sale = self.get_object()
        items_data = request.data.get('items') # [{'book_id': 1, 'quantity': 2}]
        
        if not items_data:
            return Response({'error': 'No items to return'}, status=status.HTTP_400_BAD_REQUEST)
            
        from django.db import transaction
        from .models import SaleReturn, SaleItem
        from stock.models import StockSummary, StockEntry
        
        with transaction.atomic():
            for item in items_data:
                book_id = item['book_id']
                qty = int(item['quantity'])
                
                # ১. চেক করা যে বইটা এই ইনভয়েসে ছিল কিনা
                sale_item = sale.items.filter(book_id=book_id).first()
                if not sale_item:
                    return Response({'error': f'Book ID {book_id} was not part of this sale'}, status=status.HTTP_400_BAD_REQUEST)
                
                if qty > sale_item.quantity:
                    return Response({'error': f'Cannot return more than sold quantity for Book ID {book_id}'}, status=status.HTTP_400_BAD_REQUEST)
                
                # ২. রিটার্ন রেকর্ড তৈরি
                SaleReturn.objects.create(
                    sale=sale,
                    book_id=book_id,
                    quantity=qty,
                    reason=request.data.get('reason', 'Customer Return')
                )
                
                # ৩. স্টক আপডেট (দোকানে ফিরে আসবে)
                StockSummary.update_stock(sale_item.book, qty, location='shop')
                StockEntry.objects.create(
                    book=sale_item.book,
                    quantity=qty,
                    source='return',
                    location='shop',
                    reference_id=sale.id,
                    note=f"Return from Invoice #{sale.invoice_number}"
                )
                
                # ৪. হিসাব সমন্বয় (বকেয়া থাকলে বকেয়া থেকে কমানো)
                # ফেরত দেওয়া বইয়ের দাম
                return_value = (sale_item.unit_price * qty)
                
                if sale.due_amount >= return_value:
                    sale.due_amount -= return_value
                    if sale.customer:
                        sale.customer.total_due -= return_value
                        sale.customer.save()
                else:
                    # যদি বকেয়া কম থাকে, তবে বাকি অংশ ক্যাশ রিফান্ড হিসেবে ধরা যেতে পারে
                    # এখানে আমরা শুধু বকেয়া ০ করে দিচ্ছি
                    if sale.customer:
                        sale.customer.total_due -= sale.due_amount
                        sale.customer.save()
                    sale.due_amount = 0
                
                sale.save()
                
        return Response({'message': 'Return processed successfully'})

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
