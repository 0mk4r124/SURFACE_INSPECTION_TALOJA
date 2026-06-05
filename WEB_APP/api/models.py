from django.db import models
from django.utils import timezone

# Create your models here.
class HEALTH_STATUS(models.Model):
    id = models.AutoField(db_column='ID', primary_key=True)
    location = models.CharField(db_column='LOCATION', max_length=50)
    device_type = models.CharField(db_column='DEVICE_TYPE', max_length=50, blank=True, null=True)
    ip = models.CharField(db_column='IP', max_length=50, blank=True, null=True, unique=True)
    camera_serial_number = models.CharField(db_column='CAMERA_SERIAL_NUMBER', max_length=50, blank=True, null=True)
    status = models.CharField(db_column='STATUS', max_length=20, blank=True, null=True)
    last_ping = models.DateTimeField(db_column='LAST_PING', blank=True, null=True)
    top = models.PositiveSmallIntegerField(db_column='POS_TOP', blank=True, null=True)
    left = models.PositiveSmallIntegerField(db_column='POS_LEFT', blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'HEALTH_STATUS'
        indexes = [
            models.Index(fields=["location"], name="health_location_idx"),
            models.Index(fields=["device_type"], name="health_type_idx"),
            models.Index(fields=["status"], name="health_status_idx"),
            models.Index(fields=["last_ping"], name="hs_time_idx"),
        ]

class DEFECTS_HISTORY(models.Model):
    id = models.AutoField(db_column='ID', primary_key=True)
    cycle_uid = models.CharField(db_column='CYCLE_UID', max_length=50)
    defect_detail = models.CharField(db_column='DEFECT_DETAIL', blank=True, null=True, max_length=1000)
    create_time = models.DateTimeField(db_column='CREATE_TIME', auto_now_add=True)
    img_path = models.CharField(db_column='IMG_PATH', max_length=500, blank=True, null=True)
    json_path = models.CharField(db_column='JSON_PATH', max_length=500, blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'DEFECTS_HISTORY'
        indexes = [
            models.Index(fields=["cycle_uid"], name="defect_cycle_idx"),
            models.Index(fields=["create_time"], name="defect_time_idx"),
        ]

class MILL_RUNS(models.Model):
    id = models.AutoField(db_column='ID', primary_key=True)
    roll_id = models.CharField(db_column='ROLL_ID', max_length=50)
    mill_start_time = models.DateTimeField(db_column='MILL_START_TIME', blank=True, null=True)
    mill_stop_time = models.DateTimeField(db_column='MILL_STOP_TIME', blank=True, null=True)
    created_at = models.DateTimeField(db_column='CREATED_AT', default=timezone.now)

    class Meta:
        managed = True
        db_table = 'mill_runs'

class DB_COIL_ID_SYNC(models.Model):
    id = models.AutoField(db_column='ID', primary_key=True)
    coil_id = models.CharField(db_column='COIL_ID', max_length=100)
    entry_guage_point = models.CharField(db_column='ENRTY_GUAGE_POINT', max_length=100)
    exit_guage_point = models.CharField(db_column='EXIT_GUAGE_POINT', max_length=100)
    width = models.CharField(db_column='WIDTH', max_length=100)
    alloy = models.CharField(db_column='ALLOY', max_length=100)
    created_at = models.DateTimeField(db_column='CREATED_AT', default=timezone.now)

    class Meta:
        managed = True
        db_table = 'DB_COIL_ID_SYNC'
