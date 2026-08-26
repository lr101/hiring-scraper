from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("jobs", "0006_monitoring_targets")]

    operations = [
        migrations.AlterField(
            model_name="company",
            name="domain",
            field=models.CharField(max_length=253, unique=True),
        ),
    ]
