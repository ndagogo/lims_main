from rest_framework import serializers
from .models import Centre
class CentreSerializer(serializers.ModelSerializer):
    class Meta: model=Centre; fields=['id','code','name','address','is_active']
