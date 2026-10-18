from rest_framework import serializers
from .models import (
    ImagingModality, ImagingOrder, RadiologyExam, RadiologyReport,
    RadiologyReportRevision, RadiologyReportTemplate,
)


class ImagingModalitySerializer(serializers.ModelSerializer):
    centre_name = serializers.CharField(source='centre.name', read_only=True)

    class Meta:
        model = ImagingModality
        fields = '__all__'


class ImagingOrderSerializer(serializers.ModelSerializer):
    visit_id = serializers.CharField(source='visit.visit_id', read_only=True)
    patient_name = serializers.SerializerMethodField()
    modality_name = serializers.CharField(source='modality.name', read_only=True)
    centre_id = serializers.UUIDField(source='visit.centre_id', read_only=True)

    class Meta:
        model = ImagingOrder
        fields = [
            'id', 'visit', 'visit_id', 'patient_name', 'modality', 'modality_name', 'centre_id',
            'procedure', 'priority', 'clinical_history', 'requested_by', 'status', 'scheduled_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'requested_by', 'status', 'created_at', 'updated_at']

    def get_patient_name(self, obj):
        p = obj.visit.patient
        return f'{p.first_name} {p.last_name}'.strip()

    def validate(self, attrs):
        visit = attrs.get('visit', getattr(self.instance, 'visit', None))
        modality = attrs.get('modality', getattr(self.instance, 'modality', None))
        if visit and modality and visit.centre_id != modality.centre_id:
            raise serializers.ValidationError({'modality': 'The modality must belong to the same centre as the visit.'})
        return attrs


class RadiologyExamSerializer(serializers.ModelSerializer):
    order_id = serializers.UUIDField(source='order.id', read_only=True)
    visit_id = serializers.CharField(source='order.visit.visit_id', read_only=True)
    modality = serializers.CharField(source='order.modality.name', read_only=True)
    patient_name = serializers.SerializerMethodField()

    class Meta:
        model = RadiologyExam
        fields = '__all__'
        read_only_fields = [
            'id', 'started_by', 'ended_by', 'started_at', 'ended_at', 'status',
            'created_at', 'updated_at',
        ]

    def get_patient_name(self, obj):
        p = obj.order.visit.patient
        return f'{p.first_name} {p.last_name}'.strip()


class RadiologyReportSerializer(serializers.ModelSerializer):
    exam_id = serializers.UUIDField(source='exam.id', read_only=True)
    visit_id = serializers.CharField(source='exam.order.visit.visit_id', read_only=True)
    author_name = serializers.CharField(source='author.get_full_name', read_only=True)

    class Meta:
        model = RadiologyReport
        fields = '__all__'
        read_only_fields = [
            'id', 'author', 'status', 'finalized_at', 'finalized_by', 'released_at', 'released_by',
            'template_version', 'configuration_snapshot', 'addendum',
            'last_override_by', 'last_override_at', 'last_override_reason', 'version',
            'created_at', 'updated_at',
        ]


class RadiologyReportRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RadiologyReportRevision
        fields = '__all__'


class RadiologyReportTemplateSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source='owner.get_username', read_only=True, default='')

    class Meta:
        model = RadiologyReportTemplate
        fields = '__all__'
