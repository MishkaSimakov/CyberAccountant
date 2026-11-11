import {createApp} from 'vue'
import {config} from '@fortawesome/fontawesome-svg-core'
import './style.css'
import App from './App.vue'

// Size SVG icons with Tailwind instead of injecting Font Awesome's stylesheet.
config.autoAddCss = false

createApp(App).mount('#app')
