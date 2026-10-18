from rest_framework import serializers
from .models import ReferralDoctor, ReferralInstitution, ReferralAttribution, CommissionLedgerEntry, WithdrawalRequest
class ReferralInstitutionSerializer(serializers.ModelSerializer):
    class Meta: model=ReferralInstitution; fields=['id','doctor','name','is_active','created_at','updated_at']; read_only_fields=['id','created_at','updated_at']
class ReferralDoctorSerializer(serializers.ModelSerializer):
    institutions=ReferralInstitutionSerializer(many=True,read_only=True)
    class Meta:
        model=ReferralDoctor
        fields=['id','centre','centres','doctor_code','title','first_name','last_name','facility_name','phone','email','commission_type','commission_value','is_active','notes','onboarded_by','bank_name','account_name','account_number','appear_on_reports','institutions','created_at','updated_at']
        read_only_fields=['id','created_at','updated_at']

    def _store_centres(self, doctor, centres):
        if doctor.is_default_placeholder:
            doctor.assign_centres([doctor.centre])
            return
        doctor.assign_centres(list(centres or []) or list(doctor.centres.all()))

    def create(self, validated_data):
        centres = validated_data.pop('centres', [])
        doctor = super().create(validated_data)
        self._store_centres(doctor, centres)
        return doctor

    def update(self, instance, validated_data):
        centres = validated_data.pop('centres', None)
        doctor = super().update(instance, validated_data)
        if centres is not None:
            self._store_centres(doctor, centres)
        elif not doctor.centres.filter(pk=doctor.centre_id).exists():
            doctor.centres.add(doctor.centre)
        return doctor
class ReferralAttributionSerializer(serializers.ModelSerializer):
    class Meta: model=ReferralAttribution; fields='__all__'
    def validate(self, attrs):
        visit=attrs.get('visit', getattr(self.instance,'visit',None))
        doctor=attrs.get('doctor', getattr(self.instance,'doctor',None))
        if visit and doctor and not doctor.serves_centre(visit.centre):
            raise serializers.ValidationError('Referral doctor must be assigned to the visit centre.')
        institutions=attrs.get('institutions', None)
        if institutions is not None and doctor:
            foreign=[row.name for row in institutions if row.doctor_id != doctor.id]
            if foreign:
                raise serializers.ValidationError({'institutions':'Select institutions that belong to the referring doctor.'})
        return attrs
class CommissionLedgerEntrySerializer(serializers.ModelSerializer):
    class Meta: model=CommissionLedgerEntry; fields='__all__'; read_only_fields=('created_at','updated_at')
class WithdrawalRequestSerializer(serializers.ModelSerializer):
    class Meta: model=WithdrawalRequest; fields='__all__'; read_only_fields=('status','reviewed_by','review_note','settled_at')
