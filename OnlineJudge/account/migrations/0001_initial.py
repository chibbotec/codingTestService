# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import jsonfield.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='User',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('nickname', models.CharField(max_length=100, unique=True)),
                ('role', models.CharField(default='MEMBER', max_length=20)),
                ('space_id', models.IntegerField(null=True, blank=True)),
            ],
            options={
                'db_table': 'user',
            },
        ),
        migrations.CreateModel(
            name='UserProfile',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('acm_problems_status', jsonfield.fields.JSONField(default={})),
                ('oi_problems_status', jsonfield.fields.JSONField(default={})),
                ('real_name', models.TextField(null=True)),
                ('avatar', models.TextField(default='/public/avatar/default.png')),
                ('blog', models.URLField(null=True)),
                ('mood', models.TextField(null=True)),
                ('github', models.TextField(null=True)),
                ('school', models.TextField(null=True)),
                ('major', models.TextField(null=True)),
                ('language', models.TextField(null=True)),
                ('accepted_number', models.IntegerField(default=0)),
                ('total_score', models.BigIntegerField(default=0)),
                ('submission_number', models.IntegerField(default=0)),
                ('user', models.OneToOneField(on_delete=models.CASCADE, to='account.User')),
            ],
            options={
                'db_table': 'user_profile',
            },
        ),
    ]