/**
 * Mushaf full screen — the book alone, the footer a hover away.
 *
 * Entering hides the app header (`html.mushaf-full`) and asks the browser for
 * real full screen. If the browser refuses (no user gesture, iframe policy),
 * the chrome still hides: the mushaf just scales to the window.
 *
 * A moment later the shell footer docks (`html.mushaf-dock`): it slides off
 * the bottom and the book grows into its space. A small handle pinned at the
 * bottom brings it back, overlaid on the book (`html.mushaf-dock-open`); it
 * slides away again once the pointer has left it for a moment and nothing in
 * it is in use (focus, an open drop-up, a verse being picked).
 *
 * Leaving the browser's full screen (Esc) leaves the mode too.
 */
import { mushafRepeat } from './repeat.svelte';

const FULL_CLASS = 'mushaf-full';
const DOCK_CLASS = 'mushaf-dock';
const OPEN_CLASS = 'mushaf-dock-open';
const PLAYER_SELECTOR = '.player';
/** Footer stays up this long after entering, then docks. */
const DOCK_DELAY_MS = 1200;
/** Revealed footer hides this long after the pointer leaves it. */
const HIDE_DELAY_MS = 1200;

class MushafFullscreen {
    on = $state(false);
    /** Footer slid away; the book owns its space. */
    docked = $state(false);
    /** Docked footer brought back over the book. */
    revealed = $state(false);

    private listening = false;
    private dockTimer: ReturnType<typeof setTimeout> | null = null;
    private hideTimer: ReturnType<typeof setTimeout> | null = null;
    private player: HTMLElement | null = null;

    toggle(): void {
        if (this.on) this.exit();
        else this.enter();
    }

    enter(): void {
        if (this.on) return;
        this.listen();
        this.on = true;
        document.documentElement.classList.add(FULL_CLASS);
        const req = document.documentElement.requestFullscreen?.bind(document.documentElement);
        req?.().catch((e: unknown) => {
            // Chrome-less mode still applies; only the screen takeover failed.
            console.warn('Mushaf: browser full screen refused', e);
        });
        this.dockTimer = setTimeout(() => this.dock(), DOCK_DELAY_MS);
    }

    exit(): void {
        if (!this.on) return;
        this.clearTimers();
        this.detachPlayer();
        this.on = false;
        this.docked = false;
        this.revealed = false;
        document.documentElement.classList.remove(FULL_CLASS, DOCK_CLASS, OPEN_CLASS);
        if (document.fullscreenElement) {
            document.exitFullscreen().catch((e: unknown) => console.warn('Mushaf: exit full screen failed', e));
        }
    }

    /** Bring the docked footer up over the book. */
    reveal(): void {
        if (!this.docked) return;
        if (this.hideTimer) clearTimeout(this.hideTimer);
        this.hideTimer = null;
        this.revealed = true;
        document.documentElement.classList.add(OPEN_CLASS);
        // Arm the hide check now: a pointer that never moves onto the footer
        // never fires its mouseleave. The check re-arms while it's in use.
        this.scheduleHide();
    }

    /** Slide the footer away once it's been left alone for a moment. */
    scheduleHide(): void {
        if (!this.docked || !this.revealed) return;
        if (this.hideTimer) clearTimeout(this.hideTimer);
        this.hideTimer = setTimeout(() => {
            this.hideTimer = null;
            if (this.footerBusy()) this.scheduleHide();
            else {
                this.revealed = false;
                document.documentElement.classList.remove(OPEN_CLASS);
            }
        }, HIDE_DELAY_MS);
    }

    private dock(): void {
        this.dockTimer = null;
        if (!this.on) return;
        this.attachPlayer();
        this.docked = true;
        document.documentElement.classList.add(DOCK_CLASS);
    }

    /** Something in the footer is in use: hovered, focused, a drop-up open, a verse being picked. */
    private footerBusy(): boolean {
        const p = this.player;
        if (!p) return false;
        const active = document.activeElement;
        return p.matches(':hover')
            || (!!active && active !== document.body && p.contains(active))
            || !!p.querySelector('[aria-expanded="true"]')
            || mushafRepeat.picking !== null;
    }

    private readonly onEnter = (): void => this.reveal();
    private readonly onLeave = (): void => this.scheduleHide();

    private attachPlayer(): void {
        this.player = document.querySelector<HTMLElement>(PLAYER_SELECTOR);
        this.player?.addEventListener('mouseenter', this.onEnter);
        this.player?.addEventListener('mouseleave', this.onLeave);
        this.player?.addEventListener('focusout', this.onLeave);
    }

    private detachPlayer(): void {
        this.player?.removeEventListener('mouseenter', this.onEnter);
        this.player?.removeEventListener('mouseleave', this.onLeave);
        this.player?.removeEventListener('focusout', this.onLeave);
        this.player = null;
    }

    private clearTimers(): void {
        if (this.dockTimer) clearTimeout(this.dockTimer);
        if (this.hideTimer) clearTimeout(this.hideTimer);
        this.dockTimer = null;
        this.hideTimer = null;
    }

    private listen(): void {
        if (this.listening) return;
        this.listening = true;
        document.addEventListener('fullscreenchange', () => {
            if (!document.fullscreenElement && this.on) this.exit();
        });
    }
}

export const mushafFullscreen = new MushafFullscreen();
