<?php
declare(strict_types=1);

namespace app\service;

use DOMDocument;
use DOMElement;
use DOMNode;
use think\Service;

/** Allow-list sanitizer for tenant-controlled content rendered on share pages. */
class HtmlSanitizerService extends Service
{
    private const ALLOWED_TAGS = [
        'p', 'br', 'strong', 'em', 'b', 'i', 'u', 'ul', 'ol', 'li',
        'h1', 'h2', 'h3', 'h4', 'blockquote', 'code', 'pre', 'a', 'img',
        'span', 'div', 'section',
    ];

    private const DROP_WITH_CONTENT = ['script', 'style', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'textarea', 'select', 'option', 'svg', 'math', 'meta', 'base', 'link'];

    public function register(): void
    {
        $this->app->bind('htmlSanitizerService', self::class);
    }

    public function sanitize(string $html): string
    {
        $html = trim($html);
        if ($html === '') return '';

        $document = new DOMDocument('1.0', 'UTF-8');
        $previous = libxml_use_internal_errors(true);
        $loaded = $document->loadHTML(
            '<?xml encoding="UTF-8"><div data-sanitize-root="1">' . $html . '</div>',
            LIBXML_HTML_NOIMPLIED | LIBXML_HTML_NODEFDTD
        );
        libxml_clear_errors();
        libxml_use_internal_errors($previous);
        if (!$loaded) return htmlspecialchars($html, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');

        $root = null;
        foreach ($document->getElementsByTagName('div') as $candidate) {
            if ($candidate->getAttribute('data-sanitize-root') === '1') { $root = $candidate; break; }
        }
        if (!$root) return '';
        $root->removeAttribute('data-sanitize-root');
        $this->cleanChildren($root);

        $output = '';
        foreach (iterator_to_array($root->childNodes) as $child) $output .= $document->saveHTML($child);
        return trim($output);
    }

    private function cleanChildren(DOMNode $parent): void
    {
        foreach (iterator_to_array($parent->childNodes) as $node) {
            if (!$node instanceof DOMElement) continue;
            $tag = strtolower($node->tagName);
            if (in_array($tag, self::DROP_WITH_CONTENT, true)) {
                $parent->removeChild($node);
                continue;
            }
            if (!in_array($tag, self::ALLOWED_TAGS, true)) {
                $this->cleanChildren($node);
                while ($node->firstChild) $parent->insertBefore($node->firstChild, $node);
                $parent->removeChild($node);
                continue;
            }
            $this->cleanAttributes($node, $tag);
            $this->cleanChildren($node);
        }
    }

    private function cleanAttributes(DOMElement $element, string $tag): void
    {
        $allowed = ['class', 'title'];
        if ($tag === 'a') $allowed = array_merge($allowed, ['href', 'target', 'rel']);
        if ($tag === 'img') $allowed = array_merge($allowed, ['src', 'alt', 'width', 'height']);
        foreach (iterator_to_array($element->attributes) as $attribute) {
            $name = strtolower($attribute->name);
            if (!in_array($name, $allowed, true) || str_starts_with($name, 'on')) {
                $element->removeAttributeNode($attribute);
            }
        }
        if ($element->hasAttribute('class')) {
            $class = preg_replace('/[^A-Za-z0-9 _-]/', '', $element->getAttribute('class'));
            $element->setAttribute('class', trim((string)$class));
        }
        if ($tag === 'a') {
            if (!$this->safeUrl($element->getAttribute('href'), true)) $element->removeAttribute('href');
            if ($element->getAttribute('target') === '_blank') $element->setAttribute('rel', 'noopener noreferrer');
            else $element->removeAttribute('target');
        }
        if ($tag === 'img' && !$this->safeUrl($element->getAttribute('src'), false)) $element->removeAttribute('src');
        foreach (['width', 'height'] as $dimension) {
            if ($element->hasAttribute($dimension) && !preg_match('/^[1-9][0-9]{0,3}$/', $element->getAttribute($dimension))) {
                $element->removeAttribute($dimension);
            }
        }
    }

    private function safeUrl(string $url, bool $allowRelative): bool
    {
        $url = trim(html_entity_decode($url, ENT_QUOTES | ENT_HTML5, 'UTF-8'));
        if ($url === '') return false;
        if ($allowRelative && str_starts_with($url, '/') && !str_starts_with($url, '//')) return true;
        $parts = parse_url($url);
        return is_array($parts) && strtolower((string)($parts['scheme'] ?? '')) === 'https' && !empty($parts['host']);
    }
}
