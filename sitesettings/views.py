import os
import sqlite3
import tempfile
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import FileResponse
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from users.permissions import IsAdmin
from .models import BackupLog

BACKUP_RECENCY_MINUTES = 30
RESET_CONFIRM_PHRASE = 'RESET'


def _last_backup():
    return BackupLog.objects.order_by('-created_at').first()


def _is_recent(backup):
    if not backup:
        return False
    return backup.created_at >= timezone.now() - timedelta(minutes=BACKUP_RECENCY_MINUTES)


class BackupStatusView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        backup = _last_backup()
        return Response({
            'last_backup_at': backup.created_at if backup else None,
            'is_recent': _is_recent(backup),
            'recency_minutes': BACKUP_RECENCY_MINUTES,
        })


class BackupDownloadView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        src_path = settings.DATABASES['default']['NAME']

        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(tmp_fd)

        src_conn = sqlite3.connect(src_path)
        dst_conn = sqlite3.connect(tmp_path)
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()

        BackupLog.objects.create(created_by=request.user)

        timestamp = timezone.now().strftime('%Y%m%d-%H%M%S')
        filename = f'backup-{timestamp}.sqlite3'
        response = FileResponse(open(tmp_path, 'rb'), as_attachment=True, filename=filename)
        response['X-Accel-Buffering'] = 'no'
        return response


class ResetAllDataView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request):
        confirm = request.data.get('confirm')
        if confirm != RESET_CONFIRM_PHRASE:
            return Response({'detail': 'নিশ্চিতকরণ টেক্সট সঠিক নয়।'}, status=400)

        backup = _last_backup()
        if not _is_recent(backup):
            return Response({
                'detail': f'গত {BACKUP_RECENCY_MINUTES} মিনিটের মধ্যে কোনো ব্যাকআপ পাওয়া যায়নি। আগে একটা ব্যাকআপ ডাউনলোড করুন, তারপর রিসেট করুন।'
            }, status=400)

        from accounts.models import DailyCash
        from books.models import Book, Group
        from expenses.models import ExpenseCategory
        from sales.models import Customer, Sale

        with transaction.atomic():
            Sale.objects.all().delete()
            Book.objects.all().delete()
            Group.objects.all().delete()
            Customer.objects.all().delete()
            DailyCash.objects.all().delete()
            ExpenseCategory.objects.all().delete()

        return Response({'detail': 'সব ডাটা মুছে ফেলা হয়েছে।'})
