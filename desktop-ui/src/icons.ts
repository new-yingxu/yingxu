import { defineComponent, h } from 'vue'

function symbol(glyph: string) {
  return defineComponent({
    props: { size: { type: Number, default: 18 } },
    setup(props) {
      return () => h('span', {
        'aria-hidden': 'true',
        class: 'ui-symbol',
        style: { width: `${props.size}px`, height: `${props.size}px`, fontSize: `${Math.max(10, props.size * 0.88)}px` },
      }, glyph)
    },
  })
}

export const Activity = symbol('⌁')
export const ArrowDownToLine = symbol('↓')
export const ArrowRight = symbol('→')
export const ArrowUpRight = symbol('↗')
export const Archive = symbol('▣')
export const Bell = symbol('♧')
export const Check = symbol('✓')
export const CheckCircle2 = symbol('●')
export const ChevronDown = symbol('⌄')
export const ChevronLeft = symbol('‹')
export const ChevronRight = symbol('›')
export const CircleHelp = symbol('?')
export const Clock3 = symbol('◷')
export const Cloud = symbol('☁')
export const Copy = symbol('▤')
export const Database = symbol('▦')
export const Ellipsis = symbol('···')
export const FileImage = symbol('▧')
export const FileVideo = symbol('▷')
export const Filter = symbol('≡')
export const Folder = symbol('▰')
export const HardDrive = symbol('▣')
export const Image = symbol('▧')
export const LayoutDashboard = symbol('⊞')
export const LoaderCircle = symbol('◌')
export const LockKeyhole = symbol('◉')
export const Moon = symbol('◐')
export const MoreHorizontal = symbol('···')
export const PanelLeftClose = symbol('◧')
export const Play = symbol('▷')
export const Plus = symbol('+')
export const Search = symbol('⌕')
export const Send = symbol('➤')
export const Settings2 = symbol('⚙')
export const ShieldCheck = symbol('⬟')
export const Sparkles = symbol('✦')
export const Sun = symbol('☼')
export const WandSparkles = symbol('✧')
export const X = symbol('×')
