/**
 * Mushaf full screen — the book and the shell footer, nothing else.
 *
 * Hides the app header (tabs, links, locale/theme/auth) through a class on
 * `<html>` that `MushafView`'s global rules key off, and asks the browser for
 * real full screen so the page can grow into the whole display. If the
 * browser refuses (no user gesture, iframe policy), the chrome still hides:
 * the mushaf just scales to the window instead of the screen.
 *
 * Leaving the browser's full screen (Esc) leaves the mode too.
 */
const ROOT_CLASS = 'mushaf-full';

class MushafFullscreen {
    on = $state(false);

    private listening = false;

    toggle(): void {
        if (this.on) this.exit();
        else this.enter();
    }

    enter(): void {
        if (this.on) return;
        this.listen();
        this.on = true;
        document.documentElement.classList.add(ROOT_CLASS);
        const req = document.documentElement.requestFullscreen?.bind(document.documentElement);
        req?.().catch((e: unknown) => {
            // Chrome-less mode still applies; only the screen takeover failed.
            console.warn('Mushaf: browser full screen refused', e);
        });
    }

    exit(): void {
        if (!this.on) return;
        this.on = false;
        document.documentElement.classList.remove(ROOT_CLASS);
        if (document.fullscreenElement) {
            document.exitFullscreen().catch((e: unknown) => console.warn('Mushaf: exit full screen failed', e));
        }
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
