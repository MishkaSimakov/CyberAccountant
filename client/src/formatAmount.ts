export function formatAmount(value: string | number, unit: string): string {
    const amount = Number(value)
    const formatted = Number.isFinite(amount)
        ? amount.toLocaleString('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2})
        : String(value)
    return unit === 'RUB' || unit === 'RUB_K' ? `${formatted} ₽` : formatted
}
