/** Browser-local verse bookmarks. */

import { get, writable } from 'svelte/store';

export interface Bookmark {
    surah: number;
    ayah: number;
    key: string;
    addedAt: number;
}

const LS_KEY = 'insp_bookmarks';
const SEED: Array<[number, number]> = [[1, 1], [2, 255], [36, 1]];

export const bookmarks = writable<Bookmark[]>([]);
export const bookmarksVisible = writable<boolean>(false);

export function bookmarkKey(surah: number, ayah: number): string {
    return `${surah}:${ayah}`;
}

export function isBookmarked(list: Bookmark[], key: string): boolean {
    return list.some((bookmark) => bookmark.key === key);
}

function makeBookmark(surah: number, ayah: number): Bookmark {
    return { surah, ayah, key: bookmarkKey(surah, ayah), addedAt: Date.now() };
}

function persist(list: Bookmark[]): void {
    try {
        localStorage.setItem(LS_KEY, JSON.stringify(list));
    } catch {
        // Storage can be unavailable in private mode; the in-memory store remains usable.
    }
}

function load(): Bookmark[] {
    try {
        const raw = localStorage.getItem(LS_KEY);
        if (raw === null) {
            const seeded = SEED.map(([surah, ayah]) => makeBookmark(surah, ayah));
            persist(seeded);
            return seeded;
        }
        const parsed = JSON.parse(raw) as Bookmark[];
        if (Array.isArray(parsed)) return parsed;
    } catch {
        // A corrupt or inaccessible payload degrades to an empty local list.
    }
    return [];
}

export function initBookmarks(): void {
    bookmarks.set(load());
}

export function addBookmark(surah: number, ayah: number): void {
    const key = bookmarkKey(surah, ayah);
    const list = get(bookmarks);
    if (isBookmarked(list, key)) return;
    const next = [makeBookmark(surah, ayah), ...list];
    bookmarks.set(next);
    persist(next);
}

export function removeBookmark(key: string): void {
    const next = get(bookmarks).filter((bookmark) => bookmark.key !== key);
    bookmarks.set(next);
    persist(next);
}

export function toggleBookmarksPanel(): void {
    bookmarksVisible.update((visible) => !visible);
}
