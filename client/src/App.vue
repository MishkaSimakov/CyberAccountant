<script setup lang="ts">
import {onMounted, ref} from 'vue'

const status = ref('Checking server…')
const checking = ref(false)

async function checkServer() {
  checking.value = true
  status.value = 'Checking server…'

  try {
    const response = await fetch('/api/health', {
      signal: AbortSignal.timeout(5000),
    })
    if (!response.ok) throw new Error('Health check failed')

    const result: { status: string } = await response.json()
    status.value = result.status === 'ok' ? 'Server connected' : 'Server unavailable'
  } catch {
    status.value = 'Cannot connect to the server. Please try again.'
  } finally {
    checking.value = false
  }
}

onMounted(checkServer)
</script>

<template>
  <main>
    <h1>Cyber Accountant</h1>
    <p>Your application is ready to build.</p>
    <p role="status">{{ status }}</p>
    <button :disabled="checking" @click="checkServer">Check connection</button>
  </main>
</template>
