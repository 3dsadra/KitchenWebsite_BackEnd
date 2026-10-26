from django.db import models
from django.core.exceptions import ValidationError


class MonthlyMenu(models.Model):
    year = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField()  # 1-12

    class Meta:
        unique_together = ('year', 'month')
        ordering = ['-year', '-month']

    def __str__(self):
        return f"{self.year}-{self.month:02d}"

    @classmethod
    def for_date(cls, d):
        return cls.objects.filter(year=d.year, month=d.month).first()


class MenuEntry(models.Model):
    menu = models.ForeignKey(MonthlyMenu, on_delete=models.CASCADE, related_name='slots')
    slot = models.SmallIntegerField()  # day of month
    name = models.CharField(max_length=100)
    cost = models.PositiveBigIntegerField()

    class Meta:
        unique_together = ('menu', 'slot')
        ordering = ['slot']

    def __str__(self):
        return f"Day {self.slot}: {self.name} ({self.cost})"