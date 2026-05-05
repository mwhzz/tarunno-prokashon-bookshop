from rest_framework import serializers
from .models import DailyCash, CashTransaction

class CashTransactionSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source='get_transaction_type_display', read_only=True)
    
    class Meta:
        model = CashTransaction
        fields = ['id', 'daily_cash', 'transaction_type', 'type_display', 'amount', 'note', 'timestamp', 'reference_id']
        read_only_fields = ['timestamp']

class DailyCashSerializer(serializers.ModelSerializer):
    transactions = CashTransactionSerializer(many=True, read_only=True)
    
    class Meta:
        model = DailyCash
        fields = ['id', 'date', 'opening_balance', 'closing_balance', 'is_closed', 'transactions']
