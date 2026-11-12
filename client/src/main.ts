import {createApp} from 'vue'
import {config} from '@fortawesome/fontawesome-svg-core'
import './style.css'
import App from './App.vue'
import router from './router'
import {store, storeKey} from './store'

// Size SVG icons with Tailwind instead of injecting Font Awesome's stylesheet.
config.autoAddCss = false

createApp(App).use(store, storeKey).use(router).mount('#app')
