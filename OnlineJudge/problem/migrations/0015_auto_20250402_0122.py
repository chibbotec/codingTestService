from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('problem', '0014_problem_share_submission'),
    ]

    operations = [
        migrations.AddField(
            model_name='problem',
            name='space_id',
            field=models.CharField(blank=True, db_index=True, max_length=32, null=True),
        ),
    ]