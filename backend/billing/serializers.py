from decimal import Decimal
from django.db import transaction
from rest_framework import serializers
from .models import Invoice, InvoiceItem, Payment, DiscountRequest
from laboratory.models import TestPrice, LabOrder

class InvoiceItemSerializer(serializers.ModelSerializer):
    test_name = serializers.CharField(source='test.name', read_only=True)
    class Meta:
        model = InvoiceItem
        fields = ['id','invoice','lab_order','test','test_name','tariff','description','quantity','unit_price','outsource_price','line_total','created_at']
        read_only_fields = ['id','line_total','created_at']
    def validate(self, attrs):
        invoice = attrs.get('invoice') or getattr(self.instance, 'invoice', None)
        test = attrs.get('test') or getattr(self.instance, 'test', None)
        tariff=attrs.get('tariff') or getattr(self.instance,'tariff',None)
        if invoice and invoice.status in ('PAID','CANCELLED'): raise serializers.ValidationError('Cannot change items on a paid or cancelled invoice.')
        if test and not tariff:
            raise serializers.ValidationError({'tariff':'A tariff is required so the system can enforce the configured test price.'})
        if test and tariff:
            price=TestPrice.objects.filter(test=test,tariff=tariff).first()
            if not price: raise serializers.ValidationError({'tariff':'No configured price exists for this test and tariff.'})
            supplied=attrs.get('unit_price',getattr(self.instance,'unit_price',price.price if price else None))
            if price and supplied != price.price: raise serializers.ValidationError({'unit_price':'Unit price must match the selected tariff.'})
        return attrs
    def create(self, validated_data):
        with transaction.atomic():
            item = super().create(validated_data)
            if item.lab_order and item.lab_order.visit_id != item.invoice.visit_id:
                raise serializers.ValidationError('Order and invoice must belong to the same visit.')
            return item

class InvoiceSerializer(serializers.ModelSerializer):
    items = InvoiceItemSerializer(many=True, read_only=True)
    paid_amount = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()
    class Meta:
        model = Invoice
        fields = ['id','invoice_number','visit','payer_name','subtotal','discount_amount','total','status','items','paid_amount','balance','created_at','updated_at']
        read_only_fields = ['id','invoice_number','subtotal','total','status','created_at','updated_at']
    def get_paid_amount(self, obj): return sum((p.amount for p in obj.payments.filter(status='CONFIRMED')), Decimal('0.00'))
    def get_balance(self, obj): return max(Decimal('0.00'), obj.total - self.get_paid_amount(obj))

class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ['id','invoice','amount','method','reference','received_by','receipt_number','status','created_at']
        read_only_fields = ['id','received_by','receipt_number','status','created_at']
    def validate(self, attrs):
        invoice = attrs.get('invoice') or self.instance.invoice
        amount = attrs.get('amount', self.instance.amount if self.instance else None)
        paid = sum((p.amount for p in invoice.payments.filter(status='CONFIRMED')), Decimal('0.00'))
        if invoice.status == 'CANCELLED': raise serializers.ValidationError('Cannot pay a cancelled invoice.')
        if invoice.discount_requests.filter(status='PENDING').exists():
            raise serializers.ValidationError('This invoice has a discount request waiting for accountant review. Payment is locked until it is approved or rejected.')
        if amount and amount + paid > invoice.total: raise serializers.ValidationError('Payment exceeds the outstanding invoice balance.')
        return attrs

class DiscountRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiscountRequest
        fields = '__all__'
        read_only_fields = ['id','requested_by','status','reviewed_by','review_note','applied_amount','created_at','updated_at']
    def validate(self, attrs):
        invoice = attrs.get('invoice')
        if invoice and invoice.status == 'PAID': raise serializers.ValidationError('A paid invoice cannot receive a new discount request.')
        if invoice and invoice.discount_requests.filter(status='PENDING').exists():
            raise serializers.ValidationError('A discount request is already waiting for accountant review on this invoice.')
        return attrs
