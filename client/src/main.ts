import {createApp} from 'vue'
import {config} from '@fortawesome/fontawesome-svg-core'
import './style.css'
import App from './App.vue'
import router from './router'

// Size SVG icons with Tailwind instead of injecting Font Awesome's stylesheet.
config.autoAddCss = false

createApp(App).use(router).mount('#app')
