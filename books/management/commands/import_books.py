"""
বই Import করার Command

ব্যবহার:
    python manage.py import_books books_data.csv
    python manage.py import_books books_data.csv --stock --location godown
    python manage.py import_books books_data.csv --dry-run   (test, কিছু save হবে না)
    python manage.py import_books --template                 (template CSV বানাবে)
"""

import csv
import os
import urllib.request
import urllib.error
from io import BytesIO
from decimal import Decimal, InvalidOperation
from django.core.management.base import BaseCommand, CommandError
from django.core.files.base import ContentFile
from books.models import Book, Group
from stock.models import StockEntry, StockSummary


class Command(BaseCommand):
    help = 'CSV ফাইল থেকে বই import করুন'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', nargs='?', type=str, help='CSV ফাইলের path')
        parser.add_argument('--dry-run', action='store_true', help='Test mode - কিছু save হবে না')
        parser.add_argument('--stock', action='store_true', help='stock_quantity column থেকে stock যোগ করুন')
        parser.add_argument('--location', type=str, default='godown', choices=['godown', 'shop'],
                            help='Stock location (default: godown)')
        parser.add_argument('--update', action='store_true',
                            help='একই title/isbn থাকলে update করুন (default: skip করে)')
        parser.add_argument('--template', action='store_true', help='Template CSV বানান')

    def handle(self, *args, **options):
        if options['template']:
            self._create_template()
            return

        csv_file = options.get('csv_file')
        if not csv_file:
            raise CommandError('CSV ফাইলের path দিন, অথবা --template দিয়ে template বানান')

        if not os.path.exists(csv_file):
            raise CommandError(f'ফাইল পাওয়া যায়নি: {csv_file}')

        self._import_books(csv_file, options)

    def _create_template(self):
        template_path = 'books_template.csv'
        headers = [
            'title', 'author', 'publisher', 'isbn', 'edition',
            'mrp', 'purchase_price', 'selling_price',
            'commission', 'discount', 'discount_type',
            'group', 'product_code', 'book_type',
            'stock_quantity', 'stock_location', 'image_url', 'notes'
        ]
        example = [
            'আমার ছেলেবেলা', 'হুমায়ূন আহমেদ', 'অন্যপ্রকাশ', '9789845018823', '২য়',
            '350', '245', '280',
            '0', '0', 'amount',
            'উপন্যাস', 'TP-001', 'single',
            '10', 'godown', 'https://example.com/cover.jpg', ''
        ]
        with open(template_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerow(example)

        self.stdout.write(self.style.SUCCESS(f'Template বানানো হয়েছে: {template_path}'))
        self.stdout.write('\nColumn গুলোর বিবরণ:')
        descriptions = {
            'title': 'বইয়ের নাম (অবশ্যই দিতে হবে)',
            'author': 'লেখকের নাম',
            'publisher': 'প্রকাশনী',
            'isbn': 'ISBN নম্বর (unique, না থাকলে খালি রাখুন)',
            'edition': 'সংস্করণ (যেমন: ২য়, 3rd)',
            'mrp': 'গায়ের দাম',
            'purchase_price': 'ক্রয়মূল্য',
            'selling_price': 'বিক্রয়মূল্য',
            'commission': 'কমিশন',
            'discount': 'ছাড়ের পরিমাণ',
            'discount_type': 'amount অথবা percentage',
            'group': 'ক্যাটাগরি নাম (না থাকলে নতুন বানাবে)',
            'product_code': 'প্রোডাক্ট কোড (unique)',
            'book_type': 'single অথবা series',
            'stock_quantity': 'স্টক পরিমাণ (--stock flag দিলে কাজ করবে)',
            'stock_location': 'godown অথবা shop',
            'image_url': 'বইয়ের ছবির URL (https://...jpg)',
            'notes': 'নোট/মন্তব্য',
        }
        for col, desc in descriptions.items():
            self.stdout.write(f'  {col:<20} → {desc}')

    def _import_books(self, csv_file, options):
        dry_run = options['dry_run']
        add_stock = options['stock']
        location = options['location']
        do_update = options['update']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - কিছু save হবে না\n'))

        created = 0
        updated = 0
        skipped = 0
        errors = []

        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)

            # header check
            if 'title' not in reader.fieldnames and 'Book Name' not in reader.fieldnames:
                raise CommandError("CSV-তে 'title' বা 'Book Name' column নেই। --template দিয়ে template দেখুন।")

            for row_num, row in enumerate(reader, start=2):
                row = self._normalize_row(row)
                title = row.get('title', '').strip()
                if not title:
                    errors.append(f'Row {row_num}: title খালি, skip করা হয়েছে')
                    skipped += 1
                    continue

                try:
                    result = self._process_row(row, row_num, dry_run, do_update, add_stock, location)
                    if result == 'created':
                        created += 1
                    elif result == 'updated':
                        updated += 1
                    elif result == 'skipped':
                        skipped += 1
                except Exception as e:
                    errors.append(f'Row {row_num} ({title}): {e}')
                    skipped += 1

        # Summary
        self.stdout.write('\n' + '='*50)
        self.stdout.write(self.style.SUCCESS(f'✓ নতুন বই: {created}'))
        if updated:
            self.stdout.write(self.style.SUCCESS(f'✓ আপডেট: {updated}'))
        if skipped:
            self.stdout.write(self.style.WARNING(f'⚠ Skip: {skipped}'))
        if errors:
            self.stdout.write(self.style.ERROR(f'\nErrors ({len(errors)}):'))
            for err in errors:
                self.stdout.write(self.style.ERROR(f'  - {err}'))

    def _process_row(self, row, row_num, dry_run, do_update, add_stock, default_location):
        title = row.get('title', '').strip()
        isbn = row.get('isbn', '').strip() or None
        product_code = row.get('product_code', '').strip() or None

        # existing book check (isbn > title order)
        existing = None
        if isbn:
            existing = Book.objects.filter(isbn=isbn).first()
        if not existing and product_code:
            existing = Book.objects.filter(product_code=product_code).first()
        if not existing:
            existing = Book.objects.filter(title=title).first()

        if existing and not do_update:
            self.stdout.write(f'  Row {row_num}: "{title}" আগে থেকেই আছে, skip')
            return 'skipped'

        # Group handle
        group = None
        group_name = row.get('group', '').strip()
        if group_name:
            if not dry_run:
                group, _ = Group.objects.get_or_create(name=group_name)

        # Decimal fields
        def to_decimal(val, default=0):
            try:
                return Decimal(str(val).strip()) if str(val).strip() else Decimal(default)
            except InvalidOperation:
                return Decimal(default)

        book_data = {
            'title': title,
            'author': row.get('author', '').strip(),
            'publisher': row.get('publisher', '').strip(),
            'isbn': isbn,
            'edition': row.get('edition', '').strip(),
            'mrp': to_decimal(row.get('mrp', 0)),
            'purchase_price': to_decimal(row.get('purchase_price', 0)),
            'selling_price': to_decimal(row.get('selling_price', 0)),
            'commission': to_decimal(row.get('commission', 0)),
            'discount': to_decimal(row.get('discount', 0)),
            'discount_type': row.get('discount_type', 'amount').strip() or 'amount',
            'book_type': row.get('book_type', 'single').strip() or 'single',
            'notes': row.get('notes', '').strip(),
            'group': group,
        }

        # product_code — unique, তাই সাবধানে
        if product_code:
            conflict = Book.objects.filter(product_code=product_code).exclude(
                id=existing.id if existing else 0
            ).first()
            if conflict:
                self.stdout.write(self.style.WARNING(
                    f'  Row {row_num}: product_code "{product_code}" অন্য বইতে আছে, skip করা হল'
                ))
                book_data['product_code'] = None
            else:
                book_data['product_code'] = product_code

        action = 'skip'
        if dry_run:
            action = 'update' if existing else 'create'
            self.stdout.write(f'  [DRY] Row {row_num}: {action} → "{title}"')
            return 'created' if action == 'create' else 'updated'

        if existing and do_update:
            for key, val in book_data.items():
                setattr(existing, key, val)
            existing.save()
            book = existing
            self.stdout.write(f'  ↻ Updated: "{title}"')
            action = 'updated'
        else:
            book = Book.objects.create(**book_data)
            self.stdout.write(self.style.SUCCESS(f'  + Added: "{title}"'))
            action = 'created'

        # Image download
        image_url = row.get('image_url', '').strip()
        if image_url and image_url.startswith('http') and not book.image:
            try:
                req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=15) as response:
                    image_data = response.read()
                ext = image_url.split('?')[0].rsplit('.', 1)[-1].lower()
                if ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
                    ext = 'jpg'
                filename = f"book_{book.id}.{ext}"
                book.image.save(filename, ContentFile(image_data), save=True)
                self.stdout.write(f'    → Image saved: {filename}')
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'    ⚠ Image download failed: {e}'))

        # Stock যোগ / exact sync
        if add_stock:
            godown_qty = row.get('stock_godown_quantity', '').strip()
            shop_qty = row.get('stock_shop_quantity', '').strip()
            if godown_qty or shop_qty:
                self._sync_stock(book, godown_qty, shop_qty)
                return action

            qty_str = row.get('stock_quantity', '').strip()
            loc = row.get('stock_location', '').strip() or default_location
            if loc not in ('godown', 'shop'):
                loc = default_location
            try:
                qty = int(qty_str) if qty_str else 0
                if qty > 0:
                    StockEntry.objects.create(
                        book=book,
                        quantity=qty,
                        source='purchase',
                        location=loc,
                        note='Bulk import থেকে'
                    )
                    StockSummary.update_stock(book, qty, location=loc)
            except (ValueError, TypeError):
                pass

        return action

    def _sync_stock(self, book, godown_qty, shop_qty):
        targets = {}
        for location, value in (('godown', godown_qty), ('shop', shop_qty)):
            value = str(value or '').strip()
            if not value:
                continue
            try:
                targets[location] = int(Decimal(value))
            except (InvalidOperation, ValueError):
                continue

        if not targets:
            return

        summary, _ = StockSummary.objects.get_or_create(
            book=book,
            defaults={'godown_quantity': 0, 'shop_quantity': 0},
        )

        for location, target in targets.items():
            field_name = 'godown_quantity' if location == 'godown' else 'shop_quantity'
            current = getattr(summary, field_name)
            diff = target - current
            if not diff:
                continue
            StockEntry.objects.create(
                book=book,
                quantity=diff,
                source='adjustment',
                location=location,
                purchase_price=book.purchase_price,
                note='Stock report sync',
            )
            setattr(summary, field_name, target)

        summary.save()

    def _normalize_row(self, row):
        normalized = dict(row)
        if 'Book Name' not in row:
            return normalized

        def to_int(value):
            value = str(value or '').strip()
            if not value:
                return 0
            try:
                return int(Decimal(value))
            except (InvalidOperation, ValueError):
                return 0

        godown_quantity = to_int(row.get('গোডাউন ৪তলা')) + to_int(row.get('গোডাউন ৬তলা'))
        shop_quantity = to_int(row.get('বিক্রয়কেন্দ্র'))

        normalized.update({
            'title': row.get('Book Name', ''),
            'product_code': row.get('Book Code', ''),
            'purchase_price': row.get('Production Cost', ''),
            'mrp': row.get('MRP', ''),
            'selling_price': row.get('Selling Price', ''),
            'commission': row.get('commission', '0'),
            'discount': row.get('discount', '0'),
            'discount_type': row.get('discount_type', 'amount'),
            'book_type': row.get('book_type', 'single'),
            'stock_quantity': row.get('Total Stock', ''),
            'stock_godown_quantity': str(godown_quantity),
            'stock_shop_quantity': str(shop_quantity),
            'stock_location': 'shop',
            'notes': row.get('notes', 'Imported from stock report'),
        })
        return normalized
