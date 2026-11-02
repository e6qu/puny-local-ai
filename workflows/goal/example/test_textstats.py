import unittest

from textstats import most_common, sentences, word_count, words


class TextStatsTest(unittest.TestCase):
    def test_words_are_lowercased_without_punctuation(self):
        self.assertEqual(words("Hello, world! It's 'fine'."), ["hello", "world", "it's", "fine"])

    def test_word_count(self):
        self.assertEqual(word_count("One two, three."), 3)
        self.assertEqual(word_count(""), 0)

    def test_most_common(self):
        text = "the cat and the dog and the bird"
        self.assertEqual(most_common(text, 2), [("the", 3), ("and", 2)])
        self.assertEqual(most_common("b a c a b", 3), [("a", 2), ("b", 2), ("c", 1)])

    def test_sentences(self):
        self.assertEqual(sentences("One. Two! Three? Four"), 3)
        self.assertEqual(sentences("Wait... what?"), 2)
        self.assertEqual(sentences(""), 0)


if __name__ == "__main__":
    unittest.main()
