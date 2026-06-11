from django.db import models
from django.utils import timezone


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
            models.Index(fields=['location'], name='health_location_idx'),
            models.Index(fields=['device_type'], name='health_type_idx'),
            models.Index(fields=['status'], name='health_status_idx'),
            models.Index(fields=['last_ping'], name='health_time_idx'),
            models.Index(fields=['location', 'device_type'], name='health_loc_dev_idx'),
        ]

    def __str__(self):
        return f'{self.location} | {self.device_type} | {self.status}'


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

        indexes = [
            models.Index(fields=['coil_id'], name='coil_sync_idx'),
            models.Index(fields=['created_at'], name='coil_sync_time_idx'),
        ]

    def __str__(self):
        return self.coil_id


class MILL_RUNS(models.Model):
    id = models.AutoField(db_column='ID', primary_key=True)
    cycle_uid = models.CharField(db_column='CYCLE_UID', max_length=50, unique=True)
    coil_id = models.CharField(db_column='COIL_ID', max_length=100)
    mill_start_time = models.DateTimeField(db_column='MILL_START_TIME', blank=True, null=True)
    mill_stop_time = models.DateTimeField(db_column='MILL_STOP_TIME', blank=True, null=True)
    capture_start_time = models.DateTimeField(db_column='CAPTURE_START_TIME', blank=True, null=True)
    capture_stop_time = models.DateTimeField(db_column='CAPTURE_STOP_TIME', blank=True, null=True)
    processing_start_time = models.DateTimeField(db_column='PROCESSING_START_TIME', blank=True, null=True)
    processing_stop_time = models.DateTimeField(db_column='PROCESSING_STOP_TIME', blank=True, null=True)
    capture_duration_sec = models.FloatField(db_column='CAPTURE_DURATION_SEC', blank=True, null=True)
    processing_duration_sec = models.FloatField(db_column='PROCESSING_DURATION_SEC', blank=True, null=True)
    total_cycle_duration_sec = models.FloatField(db_column='TOTAL_CYCLE_DURATION_SEC', blank=True, null=True)
    image_count = models.PositiveIntegerField(db_column='IMAGE_COUNT', default=0, blank=True, null=True)
    defect_count = models.PositiveIntegerField(db_column='DEFECT_COUNT', default=0, blank=True, null=True)
    fps = models.FloatField(db_column='FPS', blank=True, null=True)
    exposure = models.FloatField(db_column='EXPOSURE', blank=True, null=True)
    coil_meta = models.JSONField(db_column='COIL_META', blank=True, null=True)
    status = models.CharField(db_column='STATUS', max_length=20, default='RUNNING', blank=True, null=True)
    created_at = models.DateTimeField(db_column='CREATED_AT', default=timezone.now, blank=True, null=True)

    class Meta:
        managed = True

        db_table = 'MILL_RUNS'

        indexes = [
            models.Index(fields=['cycle_uid'], name='mill_cycle_idx'),
            models.Index(fields=['coil_id'], name='mill_coil_idx'),
            models.Index(fields=['status'], name='mill_status_idx'),
            models.Index(fields=['created_at'], name='mill_created_idx'),
            models.Index(fields=['mill_start_time'], name='mill_start_idx'),
            models.Index(fields=['coil_id', 'created_at'], name='mill_coil_time_idx'),
        ]

    def __str__(self):
        return f'{self.coil_id} | {self.cycle_uid}'


class DEFECTS_HISTORY(models.Model):
    id = models.AutoField(db_column='ID', primary_key=True)
    cycle_uid = models.CharField(db_column='CYCLE_UID', max_length=50)
    coil_id = models.CharField(db_column='COIL_ID', max_length=100)
    defect_details = models.CharField(db_column='DEFECT_DETAILS', max_length=3000)
    json_path = models.CharField(db_column='JSON_PATH', max_length=500)
    create_time = models.DateTimeField(db_column='CREATE_TIME', auto_now_add=True)

    class Meta:
        managed = True

        db_table = 'DEFECTS_HISTORY'

        indexes = [
            models.Index(fields=['cycle_uid'], name='defect_cycle_idx'),
            models.Index(fields=['coil_id'], name='defect_coil_idx'),
            models.Index(fields=['create_time'], name='defect_time_idx'),
            models.Index(fields=['cycle_uid', 'coil_id'], name='defect_cycle_coil_idx'),
            models.Index(fields=['coil_id', 'create_time'], name='defect_coil_time_idx'),
        ]

    def __str__(self):
        return f'{self.coil_id} | {self.cycle_uid}'