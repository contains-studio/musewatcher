"""Word-wrapped manual reply pages, using the production text renderer."""
import ctypes, subprocess, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
class ReaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        binary=Path(cls.tmp.name)/"text.so"
        subprocess.run(["cc","-shared","-fPIC","-std=c11",str(ROOT/"components/muse/muse_text.c"),"-o",str(binary)],check=True)
        cls.lib=ctypes.CDLL(str(binary))
        cls.page=cls.lib.muse_text_page
        cls.page.argtypes=[ctypes.c_char_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_char_p,ctypes.c_size_t]
        cls.page.restype=ctypes.c_int
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()
    def render(self,text,page,cols=8,lines=2):
        out=ctypes.create_string_buffer(400)
        count=self.page(text.encode(),cols,lines,page,out,len(out))
        return count,out.value.decode()
    def test_pages_preserve_all_words_and_reverse(self):
        text="one two three four five six seven eight nine ten"
        n,_=self.render(text,0)
        pages=[self.render(text,i)[1] for i in range(n)]
        self.assertEqual(" ".join(" ".join(pages).split()),text)
        self.assertEqual(self.render(text,n-1)[1],pages[-1])
        self.assertEqual(self.render(text,0)[1],pages[0])
    def test_clamps_page_and_keeps_whole_words(self):
        self.assertEqual(self.render("one two three four five",-9),(2,"one two\nthree"))
        self.assertEqual(self.render("one two three four five",999),(2,"four\nfive"))
    def test_newlines_long_words_empty_and_exact_page(self):
        self.assertEqual(self.render("abcdefghijk\nlast",0),(2,"abcdefgh\nijk"))
        self.assertEqual(self.render("abcdefghijk\nlast",1),(2,"last"))
        self.assertEqual(self.render("",0),(0,""))
        self.assertEqual(self.render("one\ntwo",0),(1,"one\ntwo"))
if __name__ == '__main__': unittest.main()
